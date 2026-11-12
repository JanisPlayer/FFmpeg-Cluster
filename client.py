import socketio
import subprocess
import argparse
import re
import requests
import os
import shlex
import threading
import time
import uuid


def str_to_bool(v):
    if isinstance(v, bool):
        return v
    if str(v).lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif str(v).lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError(f"Boolean value expected, got '{v}'.")


def build_parser():
    parser = argparse.ArgumentParser(description='Start the FFmpeg-Cluster SocketIO client.')
    parser.add_argument('--server_ip', type=str, default='localhost',
                        help='IP address of the SocketIO server (default: localhost).')
    parser.add_argument('--server_port', type=int, default=5000,
                        help='Port of the SocketIO server (default: 5000).')
    parser.add_argument('--key', type=str, default='',
                        help='Password/key for the encoding connection (default: none).')
    parser.add_argument('--ffmpeg_params_overwrite', type=str, default='',
                        help='Override server FFmpeg video encoding parameters, e.g. for hardware encoders (default: none).')
    parser.add_argument('--benchmark_frames', type=int, default=0,
                        help='Override the number of benchmark frames on this client (default: 0 = use server setting).')
    parser.add_argument('--file_path', type=str, default='',
                        help='Optional local file path or folder to read the input video from instead of HTTP.')
    parser.add_argument('--client_id', type=str, default='',
                        help='Optional persistent client identifier for reconnects (default: auto-generated).')
    parser.add_argument('--exit_on_complete', type=str_to_bool, nargs='?', const=True, default=False,
                        help='Automatically disconnect and exit when the server reports job_complete (default: False).')
    return parser


parser = build_parser()
args = parser.parse_args()

sio = socketio.Client(
    reconnection=True,
    reconnection_attempts=0,
    reconnection_delay=2,
    reconnection_delay_max=10
)

# Persistent logical client ID across reconnects
client_id = args.client_id or f"client_{uuid.uuid4().hex[:8]}"
current_segment_id = None
worker_lock = threading.Lock()
is_working = False


def resolve_input_source(data):
    """
    Determines the input video path or URL.
    If --file_path is provided and points to an existing file (or a directory containing file_name),
    uses that local path; otherwise streams over HTTP from the server (with optional auth key).
    """
    if args.file_path:
        candidate = os.path.abspath(args.file_path)
        if os.path.isfile(candidate):
            return candidate
        file_name = data.get('file_name') or os.path.basename(data.get('file_url', ''))
        if os.path.isdir(candidate) and file_name:
            joined = os.path.join(candidate, file_name)
            if os.path.isfile(joined):
                return joined
        print(f"Warning: Local path '{args.file_path}' not found; falling back to server HTTP stream.")

    file_url = f"http://{args.server_ip}:{args.server_port}{data['file_url']}"
    if args.key:
        sep = '&' if '?' in file_url else '?'
        file_url = f"{file_url}{sep}key={args.key}"
    return file_url


def build_effective_benchmark_params(data):
    """
    Constructs the FFmpeg parameter list for the benchmark, applying
    --ffmpeg_params_overwrite and --benchmark_frames if specified on the client.
    """
    if args.ffmpeg_params_overwrite:
        encode_params = shlex.split(args.ffmpeg_params_overwrite)
    elif 'encode_params' in data:
        encode_params = list(data['encode_params'])
    else:
        # Fallback for legacy servers sending only combined params
        raw_params = list(data.get('params', []))
        if len(raw_params) >= 2 and raw_params[-2] in ('-t', '-frames:v'):
            encode_params = raw_params[:-2]
        else:
            encode_params = raw_params

    if args.benchmark_frames > 0:
        bench_limit = ['-frames:v', str(args.benchmark_frames)]
    elif 'benchmark_limit' in data:
        bench_limit = list(data['benchmark_limit'])
    else:
        raw_params = list(data.get('params', []))
        if len(raw_params) >= 2 and raw_params[-2] in ('-t', '-frames:v'):
            bench_limit = raw_params[-2:]
        else:
            bench_limit = ['-t', '10']

    return encode_params + bench_limit


def build_effective_segment_params(data):
    """
    Constructs the FFmpeg parameter list for segment encoding, applying
    --ffmpeg_params_overwrite while preserving the segment filter/time arguments.
    """
    if not args.ffmpeg_params_overwrite:
        return list(data.get('params', []))

    custom_encode_params = shlex.split(args.ffmpeg_params_overwrite)
    if 'segment_params' in data:
        return custom_encode_params + list(data['segment_params'])

    # Fallback extraction of segment filters from combined params
    raw_params = list(data.get('params', []))
    for idx, token in enumerate(raw_params):
        if token in ('-vf', '-ss'):
            return custom_encode_params + raw_params[idx:]
    return custom_encode_params


def extract_effective_fps(output):
    """
    Extracts the effective encoding FPS from FFmpeg stderr output.
    First checks for FFmpeg's status line `fps= ...` or calculates `stream_fps * speed`.
    """
    fps_match = re.search(r'(\d+(?:\.\d+)?) fps', output)
    speed_match = re.findall(r'speed=\s*(\d+(?:\.\d+)?)x', output)

    if fps_match and speed_match:
        fps = float(fps_match.group(1))
        speed = float(speed_match[-1])
        effective_fps = fps * speed
        if effective_fps > 0:
            return fps, speed, effective_fps

    # Fallback: parse direct encoding `fps= 123` from progress lines
    progress_fps = re.findall(r'fps=\s*(\d+(?:\.\d+)?)', output)
    if progress_fps:
        last_fps = float(progress_fps[-1])
        if last_fps > 0:
            return last_fps, 1.0, last_fps

    # Second fallback: if stream_fps was found and encoding succeeded very quickly (speed=N/A or very short clip)
    if fps_match:
        fps = float(fps_match.group(1))
        return fps, 1.0, fps

    return None, None, None


def send_file_to_server(file_path, segment_id=None, max_retries=3):
    """Uploads the encoded segment file to the server with automatic retry on transient errors."""
    url = f"http://{args.server_ip}:{args.server_port}/upload"
    headers = {'X-Auth-Key': args.key} if args.key else {}
    data = {'client_id': client_id}
    if segment_id is not None:
        data['segment_id'] = str(segment_id)
    if args.key:
        data['key'] = args.key

    for attempt in range(1, max_retries + 1):
        try:
            with open(file_path, 'rb') as f:
                files = {'file': (os.path.basename(file_path), f)}
                response = requests.post(url, files=files, data=data, headers=headers, timeout=300)

            if response.status_code == 200:
                if os.path.exists(file_path):
                    os.remove(file_path)
                print(f"File {file_path} successfully uploaded to the server.")
                return True
            else:
                print(
                    f"Upload attempt {attempt}/{max_retries} failed with status {response.status_code}: "
                    f"{response.text}"
                )
        except Exception as e:
            print(f"Error uploading file (attempt {attempt}/{max_retries}): {e}")

        if attempt < max_retries:
            time.sleep(2 * attempt)

    if os.path.exists(file_path):
        os.remove(file_path)
    return False


@sio.event
def connect():
    print(f"Connected to server as '{client_id}'.")
    sio.emit('request_client_id', {
        'client_id': client_id,
        'key': args.key,
        'current_segment_id': current_segment_id if is_working else None
    })


@sio.event
def client_id(data):
    global client_id
    if isinstance(data, dict) and data.get('id'):
        client_id = data['id']
    print(f"Confirmed Client ID: {client_id}")


@sio.event
def start_benchmark(data):
    """Runs the FFmpeg benchmark in a background thread so the SocketIO heartbeat stays responsive."""
    def benchmark_worker():
        global is_working
        with worker_lock:
            is_working = True
        try:
            input_source = resolve_input_source(data)
            params = build_effective_benchmark_params(data)
            print(f"Starting benchmark on '{input_source}' with FFmpeg parameters: {params}")

            ffmpeg_command = [
                'ffmpeg', '-y',
                '-i', input_source,
                *params,
                '-f', 'null',
                os.devnull
            ]

            start_wall = time.time()
            result = subprocess.run(ffmpeg_command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            elapsed = max(0.001, time.time() - start_wall)
            output = result.stderr.decode(errors='replace')
            print("FFmpeg benchmark executed successfully.")

            fps, speed, effective_fps = extract_effective_fps(output)

            if not effective_fps or effective_fps <= 0:
                # Fallback calculation from frame count and elapsed wall time
                frame_matches = re.findall(r'frame=\s*(\d+)', output)
                if frame_matches:
                    frames_done = int(frame_matches[-1])
                    effective_fps = frames_done / elapsed
                    fps = effective_fps
                    speed = 1.0

            if effective_fps and effective_fps > 0:
                print(f"Extracted FPS: {fps}, Speed: {speed}x -> Effective FPS: {effective_fps:.2f}")
                sio.emit('send_fps', {'fps': effective_fps})
            else:
                print("Could not extract FPS from FFmpeg output; sending fallback FPS 25.0.")
                sio.emit('send_fps', {'fps': 25.0})

        except subprocess.CalledProcessError as e:
            err_text = e.stderr.decode(errors='replace') if e.stderr else str(e)
            print(f"An error occurred while running FFmpeg benchmark: {err_text}")
        finally:
            with worker_lock:
                is_working = False

    threading.Thread(target=benchmark_worker, daemon=True).start()


@sio.event
def adjust_segment(data):
    """Encodes an assigned video segment in a background thread and uploads the result to the server."""
    def segment_worker():
        global current_segment_id, is_working
        seg_id = data.get('segment_id', 0)

        with worker_lock:
            if is_working and current_segment_id == seg_id:
                print(f"Already working on segment #{seg_id}; ignoring duplicate request.")
                return
            is_working = True
            current_segment_id = seg_id

        input_source = resolve_input_source(data)
        params = build_effective_segment_params(data)
        safe_cid = re.sub(r'[^a-zA-Z0-9_-]', '_', str(client_id))
        output_file = f"segment_output_{safe_cid}_{seg_id}.mp4"

        if os.path.exists(output_file):
            try:
                os.remove(output_file)
            except OSError:
                pass

        # Support both input seeking (-ss before -i for fast demuxer seek when --exactly False)
        # and output filter selection (-vf select=...)
        if len(params) >= 2 and params[0] == '-ss':
            # Place -ss (and -to if present) appropriately for accurate and fast seeking
            ffmpeg_command = [
                'ffmpeg', '-y',
                '-i', input_source,
                *params,
                output_file
            ]
        else:
            ffmpeg_command = [
                'ffmpeg', '-y',
                '-i', input_source,
                *params,
                output_file
            ]

        print(f"Encoding segment #{seg_id} -> {output_file} with params: {params}")

        max_encode_attempts = 2
        for attempt in range(1, max_encode_attempts + 1):
            try:
                start_wall = time.time()
                result = subprocess.run(ffmpeg_command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                elapsed = max(0.001, time.time() - start_wall)
                output = result.stderr.decode(errors='replace')
                print(f"FFmpeg segment #{seg_id} encoded successfully.")

                fps, speed, effective_fps = extract_effective_fps(output)
                if not effective_fps or effective_fps <= 0:
                    frame_matches = re.findall(r'frame=\s*(\d+)', output)
                    if frame_matches:
                        effective_fps = int(frame_matches[-1]) / elapsed
                    else:
                        effective_fps = 25.0

                print(f"Segment #{seg_id} Effective FPS: {effective_fps:.2f}")
                sio.emit('finish', {'fps': effective_fps, 'segment_id': seg_id})

                uploaded = send_file_to_server(output_file, segment_id=seg_id)
                if not uploaded:
                    sio.emit('segment_error', {
                        'segment_id': seg_id,
                        'error': 'Failed to upload segment file after retries'
                    })
                break

            except subprocess.CalledProcessError as e:
                err_text = e.stderr.decode(errors='replace') if e.stderr else str(e)
                print(f"Error running FFmpeg on segment #{seg_id} (attempt {attempt}/{max_encode_attempts}): {err_text}")
                if os.path.exists(output_file):
                    try:
                        os.remove(output_file)
                    except OSError:
                        pass
                if attempt == max_encode_attempts:
                    sio.emit('segment_error', {
                        'segment_id': seg_id,
                        'error': err_text[-500:] if err_text else 'FFmpeg process failed'
                    })
                else:
                    time.sleep(1)

        with worker_lock:
            is_working = False
            current_segment_id = None

    threading.Thread(target=segment_worker, daemon=True).start()


@sio.event
def job_complete(data):
    out = data.get('output_file', 'output.mp4') if isinstance(data, dict) else 'output.mp4'
    print(f"Server finished combining all segments into '{out}'.")
    if args.exit_on_complete:
        print("Exiting client (--exit_on_complete is set).")
        sio.disconnect()


@sio.event
def disconnect():
    print("Disconnected from server.")


if __name__ == '__main__':
    server_url = f'http://{args.server_ip}:{args.server_port}'
    print(f"Connecting to server at {server_url} (Client ID: {client_id})...")
    auth_payload = {'client_id': client_id}
    if args.key:
        auth_payload['key'] = args.key
    headers = {'X-Auth-Key': args.key} if args.key else {}
    sio.connect(server_url, auth=auth_payload, headers=headers)
    sio.wait()
