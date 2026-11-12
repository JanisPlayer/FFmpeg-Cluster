from flask import Flask, send_from_directory, abort, request, jsonify
from flask_socketio import SocketIO, disconnect
import argparse
import os
import shlex
import subprocess
import math
import threading
import time
import hmac
from fractions import Fraction
import numpy as np


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
    parser = argparse.ArgumentParser(description='Start the FFmpeg-Cluster Flask-SocketIO server.')
    parser.add_argument('--required_clients', type=int, default=3,
                        help='Number of clients required to start the benchmark (default: 3).')
    parser.add_argument('--benchmark_seconds', type=int, default=10,
                        help='Number of seconds from the video to benchmark (default: 10). Set to 0 to skip benchmark.')
    parser.add_argument('--benchmark_frames', type=int, default=0,
                        help='Override benchmark duration with a specific number of frames (default: 0 = disabled).')
    parser.add_argument('--file_name', type=str, required=True,
                        help='Path or name of the input video file to be served and encoded.')
    parser.add_argument('--ffmpeg_params', type=str, default='-c:v libx264 -preset fast',
                        help='FFmpeg encoding parameters (default: "-c:v libx264 -preset fast").')
    parser.add_argument('--file_name_output', type=str, default='output.mp4',
                        help='Name or path of the output file (default: output.mp4).')
    parser.add_argument('--port', type=int, default=5000,
                        help='Port to run the Flask-SocketIO server (default: 5000).')
    parser.add_argument('--exactly', type=str_to_bool, nargs='?', const=True, default=True,
                        help='Use exact frame counting method (default: True). Set to False for fast time/frame estimation.')
    parser.add_argument('--streaming', type=str_to_bool, nargs='?', const=True, default=False,
                        help='Enable HLS live streaming mode (default: False).')
    parser.add_argument('--streaming_delay', type=int, default=30,
                        help='Delay in seconds for the livestream buffer to avoid interruptions (default: 30).')
    parser.add_argument('--streamingffmpeg_params', type=str,
                        default='-hls_time 6 -hls_flags delete_segments -hls_playlist_type event -hls_segment_type fmp4 -hls_fmp4_init_filename init.mp4 -master_pl_name master.m3u8 output.m3u8',
                        help='Streaming FFmpeg encoding parameters.')
    parser.add_argument('--segment_time', type=float, default=None,
                        help='Specify the duration of each segment in seconds. Negative values use the weakest client as baseline.')
    parser.add_argument('--segment_time_for_client', type=float, default=10.0,
                        help='Default segment duration in seconds when benchmark is off or segment_time is not set (default: 10).')
    parser.add_argument('--segment_request', type=str_to_bool, nargs='?', const=True, default=False,
                        help='Dynamic mode where clients continuously request/receive new segments until the video is finished (default: False).')
    parser.add_argument('--key', type=str, default='',
                        help='Password/key for authenticating client connections and file transfers (default: none).')
    return parser


def split_ffmpeg_params(params_str):
    """
    Splits FFmpeg parameters into video/general parameters (for segment encoding on clients)
    and audio parameters (for final muxing/encoding on the server).
    """
    tokens = shlex.split(params_str)
    video_params = []
    audio_params = []

    audio_flags_with_arg = {
        '-c:a', '-codec:a', '-acodec',
        '-b:a', '-ab',
        '-ar', '-ac',
        '-af', '-filter:a',
        '-q:a', '-aq',
        '-profile:a', '-disposition:a'
    }
    audio_flags_no_arg = {'-an'}

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in audio_flags_with_arg and i + 1 < len(tokens):
            audio_params.extend([token, tokens[i + 1]])
            i += 2
        elif token in audio_flags_no_arg:
            audio_params.append(token)
            i += 1
        else:
            video_params.append(token)
            i += 1

    return video_params, audio_params


app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('FFMPEG_CLUSTER_SECRET', 'ffmpeg-cluster-secret-key')

# Use threading mode with generous ping timeouts to prevent server freezes or disconnects during heavy encoding
socketio = SocketIO(
    app,
    cors_allowed_origins='*',
    async_mode='threading',
    ping_timeout=120,
    ping_interval=25,
    max_http_buffer_size=500 * 1024 * 1024
)

# Synchronization primitives
state_lock = threading.RLock()
video_info_event = threading.Event()

# Global configuration (populated in main or init_server)
REQUIRED_CLIENTS = 3
BENCHMARK_SECONDS = 10
BENCHMARK_FRAMES = 0
FILE_PATH = ''
FILE_DIR = '.'
FILE_BASENAME = ''
FILE_NAME_OUTPUT = 'output.mp4'
PORT = 5000
EXACTLY = True
STREAMING = False
STREAMING_DELAY = 30
STREAMING_FFMPEG_PARAMS = []
SEGMENT_TIME = None
SEGMENT_TIME_FOR_CLIENT = 10.0
SEGMENT_REQUEST = False
AUTH_KEY = ''

FFMPEG_PARAMS = []
VIDEO_FFMPEG_PARAMS = []
AUDIO_FFMPEG_PARAMS = []

UPLOAD_FOLDER = 'uploads'
STREAM_FOLDER = 'stream'
MAX_SEGMENT_RETRIES = 3
RECONNECT_GRACE_SECONDS = 20

# Video metadata
video_fps = None
video_duration = None
total_frames = None
video_has_audio = False

# Cluster state
# logical_client_id -> {
#   'sid': str,
#   'connected': bool,
#   'fps': float or None,
#   'current_segment_id': int or None,
#   'last_seen': float,
#   'state': 'idle' | 'benchmarking' | 'encoding' | 'done'
# }
clients_info = {}
sid_to_client = {}

# Segment tracking
# segment_id -> {
#   'segment_id': int,
#   'start_frame': int,
#   'end_frame': int,
#   'start_time': float,
#   'end_time': float,
#   'is_last': bool,
#   'filename': str,
#   'assigned_to': str or None,
#   'status': 'pending' | 'in_progress' | 'completed',
#   'retries': int,
#   'encode_params': list,
#   'segment_params': list,
#   'params': list
# }
segments = {}
pending_retry_segments = []

# Dynamic segment pointer (protected by state_lock)
next_frame_cursor = 0
next_time_cursor = 0.0
next_segment_id = 0
all_segments_generated = False
encoding_started = False
benchmark_started = False
combining_in_progress = False
job_completed = False

# Streaming state
stream_started_at = None
stream_accumulated_duration = 0.0
stream_next_index = 0
stream_process_lock = threading.Lock()


def verify_key(provided_key):
    """Constant-time verification of the authentication key if AUTH_KEY is set."""
    if not AUTH_KEY:
        return True
    if not provided_key:
        return False
    return hmac.compare_digest(str(provided_key), str(AUTH_KEY))


def get_request_key(auth_payload=None):
    """Extracts the authentication key from SocketIO auth payload, headers, form data, or query args."""
    if isinstance(auth_payload, dict) and 'key' in auth_payload:
        return auth_payload.get('key')
    header_key = request.headers.get('X-Auth-Key')
    if header_key:
        return header_key
    form_key = request.form.get('key')
    if form_key:
        return form_key
    return request.args.get('key', '')


def parse_fraction_fps(rate_str):
    """Safely parses an ffprobe frame rate string like '30000/1001' or '25/1' without eval()."""
    rate_str = (rate_str or '').strip()
    if not rate_str or rate_str == '0/0':
        return 25.0
    try:
        return float(Fraction(rate_str))
    except (ValueError, ZeroDivisionError):
        return 25.0


def check_has_audio(video_file):
    """Checks whether the input video file contains at least one audio stream."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'a',
        '-show_entries', 'stream=index',
        '-of', 'csv=p=0',
        video_file
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        return bool(res.stdout.strip())
    except Exception:
        return False


def refine_end_duration(video_file, rough_duration, fps):
    """
    Refines the video duration and estimated frame count when --exactly is False
    by inspecting only the last few seconds of packets/frames via ffprobe -read_intervals.
    """
    if rough_duration <= 0:
        return rough_duration, math.ceil(fps * max(rough_duration, 1.0))

    seek_start = max(0.0, rough_duration - 3.0)
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-read_intervals', f'{seek_start:.2f}%+10',
        '-show_entries', 'packet=pts_time,duration_time',
        '-of', 'csv=p=0',
        video_file
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        max_end_time = rough_duration
        for line in lines:
            parts = line.split(',')
            try:
                pts = float(parts[0])
                dur = float(parts[1]) if len(parts) > 1 and parts[1] not in ('N/A', '') else (1.0 / fps)
                if pts + dur > max_end_time:
                    max_end_time = pts + dur
            except (ValueError, IndexError):
                continue
        precise_frames = max(1, int(round(max_end_time * fps)))
        return max_end_time, precise_frames
    except Exception:
        return rough_duration, max(1, math.ceil(fps * rough_duration))


def get_video_info(video_file, exactly=False):
    """
    Uses ffprobe to extract video FPS, duration, and total frame count.
    If exactly is True, counts exact frames with -count_frames.
    If exactly is False, estimates frames from stream/format metadata and refines the end timestamp.
    """
    print(f"Using ffprobe to extract video information from '{video_file}' (exactly={exactly})...")
    if exactly:
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-count_frames',
            '-show_entries', 'stream=r_frame_rate,duration,nb_read_frames:format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=0',
            video_file
        ]
    else:
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=r_frame_rate,duration,nb_frames:format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=0',
            video_file
        ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    info = {}
    for line in result.stdout.splitlines():
        if '=' in line:
            k, v = line.strip().split('=', 1)
            if k not in info or info[k] in ('N/A', ''):
                info[k] = v

    fps = parse_fraction_fps(info.get('r_frame_rate', '25/1'))
    duration_str = info.get('duration', 'N/A')
    try:
        duration = float(duration_str)
    except (ValueError, TypeError):
        duration = 0.0

    if exactly:
        nb_read = info.get('nb_read_frames', 'N/A')
        try:
            frames = int(nb_read)
        except (ValueError, TypeError):
            frames = max(1, int(round(fps * duration)))
        if duration <= 0 and fps > 0:
            duration = frames / fps
        return fps, duration, frames
    else:
        nb_frames_str = info.get('nb_frames', 'N/A')
        duration, refined_frames = refine_end_duration(video_file, duration, fps)
        try:
            header_frames = int(nb_frames_str)
            frames = header_frames if abs(header_frames - refined_frames) <= 2 else refined_frames
        except (ValueError, TypeError):
            frames = refined_frames
        return fps, duration, frames


def async_ffprobe(video_file, exactly=False):
    """Starts ffprobe analysis asynchronously and sets video_info_event when complete."""
    def run_ffprobe():
        global video_fps, video_duration, total_frames, video_has_audio
        try:
            fps, dur, frames = get_video_info(video_file, exactly)
            has_aud = check_has_audio(video_file)
            with state_lock:
                video_fps = fps
                video_duration = dur
                total_frames = frames
                video_has_audio = has_aud
            print(f"Video info ready: FPS={fps:.3f}, Duration={dur:.2f}s, Total Frames={frames}, Audio={has_aud}")
        except Exception as e:
            print(f"Error extracting video info with ffprobe: {e}")
        finally:
            video_info_event.set()

    threading.Thread(target=run_ffprobe, daemon=True).start()


def wait_for_video_info():
    """Waits until the asynchronous ffprobe thread has populated video metadata."""
    if not video_info_event.is_set():
        print("Waiting for video information from ffprobe...")
        video_info_event.wait()


def validate_segment_file(filepath):
    """Validates that an uploaded video segment exists, is non-empty, and has a valid video stream."""
    if not os.path.isfile(filepath) or os.path.getsize(filepath) == 0:
        return False
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_type',
        '-of', 'csv=p=0',
        filepath
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
        return 'video' in res.stdout.lower()
    except Exception:
        return False


def build_segment_params(start_frame, end_frame, start_time, end_time, is_last):
    """
    Builds the FFmpeg filter/seeking parameters for a segment.
    - When EXACTLY is True: uses frame-exact selection (-vf select=...).
      For the very last segment, uses gte(n, start_frame) so no trailing frames are missed.
    - When EXACTLY is False: uses time intervals (-ss / -to) and omits -to on the last segment
      to encode cleanly to the end of the video without duplicate frames.
    """
    if EXACTLY:
        if is_last:
            select_expr = fr"select=gte(n\,{start_frame})"
        else:
            select_expr = fr"select=between(n\,{start_frame}\,{end_frame})"
        seg_params = [
            '-vf', select_expr,
            '-vsync', 'vfr',
            '-an'
        ]
        full_params = VIDEO_FFMPEG_PARAMS + seg_params
        return VIDEO_FFMPEG_PARAMS, seg_params, full_params
    else:
        seg_params = ['-ss', f"{start_time:.6f}"]
        if not is_last:
            seg_params.extend(['-to', f"{end_time:.6f}"])
        seg_params.extend(['-copyts', '-start_at_zero', '-an'])
        full_params = VIDEO_FFMPEG_PARAMS + seg_params
        return VIDEO_FFMPEG_PARAMS, seg_params, full_params


def emit_segment_to_client(client_id, segment):
    """Emits the adjust_segment event to the specified logical client."""
    with state_lock:
        cinfo = clients_info.get(client_id)
        if not cinfo or not cinfo['connected']:
            return False
        sid = cinfo['sid']
        cinfo['current_segment_id'] = segment['segment_id']
        cinfo['state'] = 'encoding'
        segment['assigned_to'] = client_id
        segment['status'] = 'in_progress'

    file_url = f'/files/{FILE_BASENAME}'
    payload = {
        'file_url': file_url,
        'file_name': FILE_BASENAME,
        'segment_id': segment['segment_id'],
        'start_frame': segment['start_frame'],
        'end_frame': segment['end_frame'],
        'start_time': segment['start_time'],
        'end_time': segment['end_time'],
        'is_last': segment['is_last'],
        'encode_params': segment['encode_params'],
        'segment_params': segment['segment_params'],
        'params': segment['params']
    }
    print(
        f"Assigning segment #{segment['segment_id']} to client {client_id}: "
        f"frames {segment['start_frame']}..{segment['end_frame']} "
        f"({segment['start_time']:.2f}s..{segment['end_time']:.2f}s) -> {segment['filename']}"
    )
    socketio.emit('adjust_segment', payload, to=sid)
    return True


def compute_client_dynamic_share_frames(client_id):
    """
    Calculates how many frames a client should receive for its next dynamic segment
    based on --segment_time, --segment_time_for_client, and benchmark results.
    """
    active_fps = {
        cid: info['fps']
        for cid, info in clients_info.items()
        if info.get('fps') is not None and info.get('fps') > 0
    }

    # Mode without benchmark (--benchmark_seconds 0 and --benchmark_frames 0) or no FPS data
    if (BENCHMARK_SECONDS <= 0 and BENCHMARK_FRAMES <= 0) or client_id not in active_fps:
        base_sec = abs(SEGMENT_TIME) if SEGMENT_TIME is not None and SEGMENT_TIME != 0 else SEGMENT_TIME_FOR_CLIENT
        return max(1, int(round(base_sec * video_fps)))

    c_fps = active_fps[client_id]
    min_fps = min(active_fps.values())
    avg_fps = sum(active_fps.values()) / len(active_fps)

    if SEGMENT_TIME is not None:
        if SEGMENT_TIME < 0:
            # Negative segment_time: weakest client gets abs(segment_time) seconds as baseline
            base_sec = abs(SEGMENT_TIME)
            client_sec = base_sec * (c_fps / min_fps) if min_fps > 0 else base_sec
        else:
            # Positive segment_time with benchmark: scale segment_time by client capacity relative to average
            base_sec = SEGMENT_TIME
            client_sec = base_sec * (c_fps / avg_fps) if avg_fps > 0 else base_sec
    else:
        base_sec = SEGMENT_TIME_FOR_CLIENT
        client_sec = base_sec * (c_fps / avg_fps) if avg_fps > 0 else base_sec

    return max(1, int(round(client_sec * video_fps)))


def create_next_dynamic_segment(client_id):
    """
    Thread-safely allocates the next unprocessed segment of the video for `client_id`
    in --segment_request / --streaming mode.
    Follows the README specification:
    - If fewer than 10 seconds (in frames) remain, the client receives all remaining frames.
    - Otherwise, it receives its benchmark-determined or configured portion.
    """
    global next_frame_cursor, next_time_cursor, next_segment_id, all_segments_generated

    with state_lock:
        # First check if any failed/abandoned segment needs to be retried
        while pending_retry_segments:
            retry_seg_id = pending_retry_segments.pop(0)
            seg = segments.get(retry_seg_id)
            if seg and seg['status'] == 'pending':
                return seg

        if all_segments_generated or next_frame_cursor >= total_frames:
            all_segments_generated = True
            return None

        remaining_frames = total_frames - next_frame_cursor
        ten_seconds_frames = max(1, int(round(10.0 * video_fps)))
        share_frames = compute_client_dynamic_share_frames(client_id)

        # If fewer than 10 seconds remain in the video (or remaining after this segment < 10s),
        # assign all remaining frames to avoid tiny tail fragments
        if remaining_frames <= ten_seconds_frames or remaining_frames <= share_frames or (remaining_frames - share_frames) < ten_seconds_frames:
            assigned_frames = remaining_frames
            is_last = True
        else:
            assigned_frames = share_frames
            is_last = False

        start_frame = next_frame_cursor
        end_frame = start_frame + assigned_frames - 1
        start_time = next_time_cursor
        client_duration = assigned_frames / video_fps
        end_time = video_duration if is_last else min(video_duration, start_time + client_duration)

        next_frame_cursor = end_frame + 1
        next_time_cursor = end_time
        if next_frame_cursor >= total_frames:
            is_last = True
            all_segments_generated = True

        seg_id = next_segment_id
        next_segment_id += 1

        encode_params, segment_params, full_params = build_segment_params(
            start_frame, end_frame, start_time, end_time, is_last
        )

        seg = {
            'segment_id': seg_id,
            'start_frame': start_frame,
            'end_frame': end_frame,
            'start_time': start_time,
            'end_time': end_time,
            'is_last': is_last,
            'filename': f"segment_{seg_id:05d}.mp4",
            'assigned_to': client_id,
            'status': 'pending',
            'retries': 0,
            'encode_params': encode_params,
            'segment_params': segment_params,
            'params': full_params
        }
        segments[seg_id] = seg
        return seg


def assign_work_to_client(client_id):
    """Assigns the next available segment (retry queue or dynamic cursor) to a client."""
    with state_lock:
        if job_completed:
            return False

        # Always prioritize pending retry segments first (works in both static and dynamic modes)
        while pending_retry_segments:
            retry_seg_id = pending_retry_segments.pop(0)
            seg = segments.get(retry_seg_id)
            if seg and seg['status'] == 'pending':
                return emit_segment_to_client(client_id, seg)

        if SEGMENT_REQUEST or STREAMING:
            seg = create_next_dynamic_segment(client_id)
            if seg:
                return emit_segment_to_client(client_id, seg)

        cinfo = clients_info.get(client_id)
        if cinfo:
            cinfo['state'] = 'done'
            cinfo['current_segment_id'] = None
        return False


def adjust_segments(v_fps, v_duration, t_frames):
    """
    Initializes segment distribution across connected clients once video info and (optional)
    benchmark results are ready.
    """
    global encoding_started, next_frame_cursor, next_time_cursor, next_segment_id, all_segments_generated

    with state_lock:
        if encoding_started:
            return
        encoding_started = True

        connected_cids = [cid for cid, info in clients_info.items() if info['connected']]
        if not connected_cids:
            print("No connected clients available to start encoding.")
            encoding_started = False
            return

        # If benchmark was skipped or a client has no FPS recorded, assign equal weight (1.0)
        for cid in connected_cids:
            if not clients_info[cid].get('fps') or clients_info[cid]['fps'] <= 0:
                clients_info[cid]['fps'] = v_fps

        fps_values = [clients_info[cid]['fps'] for cid in connected_cids]
        total_fps_capacity = float(np.sum(fps_values))
        encode_speed = total_fps_capacity / v_fps if v_fps > 0 else 1.0
        encoding_time = t_frames / total_fps_capacity if total_fps_capacity > 0 else 0.0

        print(f"Video FPS: {v_fps:.3f}")
        print(f"Video duration: {v_duration:.2f} seconds")
        print(f"Total frames: {t_frames}")
        print(f"Total FPS capacity: {total_fps_capacity:.2f}")
        print(f"Estimated encoding time: {encoding_time:.2f} seconds (speed x{encode_speed:.2f})")

        if SEGMENT_REQUEST or STREAMING:
            print("Starting dynamic segment distribution (segment_request / streaming mode)...")
            for cid in connected_cids:
                assign_work_to_client(cid)
            return

        # Classic static distribution: one proportional segment per connected client
        current_time = 0.0
        current_frame = 0

        for idx, cid in enumerate(connected_cids):
            is_last = (idx == len(connected_cids) - 1)
            client_capacity_percentage = clients_info[cid]['fps'] / total_fps_capacity

            if is_last:
                start_frame = current_frame
                end_frame = max(start_frame, t_frames - 1)
                start_time = current_time
                end_time = v_duration
                client_frame_share = end_frame - start_frame + 1
                client_duration = max(0.0, end_time - start_time)
            else:
                client_frame_share = max(1, int(round(client_capacity_percentage * t_frames)))
                start_frame = current_frame
                end_frame = min(t_frames - 1, start_frame + client_frame_share - 1)
                client_duration = (end_frame - start_frame + 1) / v_fps
                start_time = current_time
                end_time = min(v_duration, start_time + client_duration)

                current_frame = end_frame + 1
                current_time = end_time

            seg_id = next_segment_id
            next_segment_id += 1

            encode_params, segment_params, full_params = build_segment_params(
                start_frame, end_frame, start_time, end_time, is_last
            )

            seg = {
                'segment_id': seg_id,
                'start_frame': start_frame,
                'end_frame': end_frame,
                'start_time': start_time,
                'end_time': end_time,
                'is_last': is_last,
                'filename': f"segment_{seg_id:05d}.mp4",
                'assigned_to': cid,
                'status': 'pending',
                'retries': 0,
                'encode_params': encode_params,
                'segment_params': segment_params,
                'params': full_params
            }
            segments[seg_id] = seg
            emit_segment_to_client(cid, seg)

        all_segments_generated = True


def update_hls_stream():
    """
    When --streaming is enabled, feeds completed contiguous segments into an HLS playlist
     inside STREAM_FOLDER once the --streaming_delay buffer threshold is satisfied.
    """
    global stream_started_at, stream_accumulated_duration, stream_next_index

    if not STREAMING:
        return

    with stream_process_lock:
        os.makedirs(STREAM_FOLDER, exist_ok=True)
        with state_lock:
            ready_segments = []
            idx = 0
            while idx in segments and segments[idx]['status'] == 'completed':
                seg = segments[idx]
                ready_segments.append(seg)
                idx += 1

            if not ready_segments:
                return

            total_ready_dur = sum(max(0.0, s['end_time'] - s['start_time']) for s in ready_segments)
            all_done = all_segments_generated and len(ready_segments) == len(segments)

            if total_ready_dur < STREAMING_DELAY and not all_done:
                print(f"[Streaming] Buffered {total_ready_dur:.1f}s / {STREAMING_DELAY}s delay...")
                return

            if len(ready_segments) <= stream_next_index and not all_done:
                return

            stream_next_index = len(ready_segments)

        list_path = os.path.join(STREAM_FOLDER, 'stream_filelist.txt')
        with open(list_path, 'w', encoding='utf-8') as f:
            for seg in ready_segments:
                abs_seg = os.path.abspath(os.path.join(UPLOAD_FOLDER, seg['filename'])).replace('\\', '/')
                f.write(f"file '{abs_seg}'\n")

        cmd = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', os.path.abspath(list_path),
            '-c:v', 'copy',
            *STREAMING_FFMPEG_PARAMS
        ]
        try:
            subprocess.run(cmd, cwd=STREAM_FOLDER, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print(f"[Streaming] Updated HLS stream with {len(ready_segments)} segment(s) at /stream/output.m3u8")
        except subprocess.CalledProcessError as e:
            print(f"[Streaming] Error updating HLS stream: {e.stderr.decode(errors='replace')}")


def combine_segments(segment_files):
    """
    Combines all encoded video segments into FILE_NAME_OUTPUT and applies audio parameters
    (or copies original audio if no audio conversion parameters were specified).
    """
    global combining_in_progress, job_completed

    output_file = os.path.abspath(FILE_NAME_OUTPUT)
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    filelist_path = os.path.abspath('filelist.txt')
    with open(filelist_path, 'w', encoding='utf-8') as f:
        for segment in segment_files:
            abs_seg = os.path.abspath(segment).replace('\\', '/')
            f.write(f"file '{abs_seg}'\n")

    disable_audio = ('-an' in AUDIO_FFMPEG_PARAMS) or (not video_has_audio)

    if not disable_audio:
        audio_encoding_args = AUDIO_FFMPEG_PARAMS if AUDIO_FFMPEG_PARAMS else ['-c:a', 'copy']
        ffmpeg_command = [
            'ffmpeg', '-y',
            '-i', FILE_PATH,
            '-f', 'concat',
            '-safe', '0',
            '-i', filelist_path,
            '-map', '1:v:0',
            '-map', '0:a?',
            '-c:v', 'copy',
            *audio_encoding_args,
            output_file
        ]
    else:
        ffmpeg_command = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', filelist_path,
            '-c', 'copy',
            output_file
        ]

    try:
        subprocess.run(ffmpeg_command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"Successfully combined {len(segment_files)} segment(s) into '{output_file}'.")

        for segment in segment_files:
            if os.path.exists(segment):
                os.remove(segment)
                print(f"Deleted temporary segment file: {segment}")

        if os.path.exists(filelist_path):
            os.remove(filelist_path)
            print("Deleted filelist.txt.")

        with state_lock:
            job_completed = True
            combining_in_progress = False
        socketio.emit('job_complete', {'output_file': FILE_NAME_OUTPUT})

    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode(errors='replace') if e.stderr else str(e)
        print(f"An error occurred while combining segments: {err_msg}")
        with state_lock:
            combining_in_progress = False


def check_and_combine_if_complete():
    """Checks if all segments have been generated and uploaded, then triggers combine_segments."""
    global combining_in_progress

    with state_lock:
        if not all_segments_generated or not segments or combining_in_progress or job_completed:
            return
        if not all(seg['status'] == 'completed' for seg in segments.values()):
            return

        ordered_ids = sorted(segments.keys())
        segment_files = [os.path.join(UPLOAD_FOLDER, segments[sid]['filename']) for sid in ordered_ids]
        if not all(os.path.isfile(p) for p in segment_files):
            return

        combining_in_progress = True

    print("All segments uploaded and verified. Starting to combine segments...")
    threading.Thread(target=combine_segments, args=(segment_files,), daemon=True).start()


def send_benchmark_to_client(client_id):
    """Sends the start_benchmark command to a single logical client."""
    with state_lock:
        cinfo = clients_info.get(client_id)
        if not cinfo or not cinfo['connected']:
            return
        sid = cinfo['sid']
        cinfo['state'] = 'benchmarking'

    file_url = f'/files/{FILE_BASENAME}'
    if BENCHMARK_FRAMES > 0:
        bench_limit = ['-frames:v', str(BENCHMARK_FRAMES)]
    else:
        bench_limit = ['-t', str(BENCHMARK_SECONDS)]

    params = VIDEO_FFMPEG_PARAMS + bench_limit
    payload = {
        'file_url': file_url,
        'file_name': FILE_BASENAME,
        'encode_params': VIDEO_FFMPEG_PARAMS,
        'benchmark_limit': bench_limit,
        'benchmark_seconds': BENCHMARK_SECONDS,
        'benchmark_frames': BENCHMARK_FRAMES,
        'params': params
    }
    print(f"Sending start_benchmark to client {client_id}")
    socketio.emit('start_benchmark', payload, to=sid)


def start_benchmark_for_all_clients():
    """Starts the benchmark (or directly starts encoding if benchmark is disabled) for all connected clients."""
    global benchmark_started

    with state_lock:
        if benchmark_started or encoding_started:
            return
        benchmark_started = True
        connected_cids = [cid for cid, info in clients_info.items() if info['connected']]

    # Start asynchronous ffprobe analysis right away so it runs in parallel with the client benchmark
    async_ffprobe(FILE_PATH, EXACTLY)

    if BENCHMARK_SECONDS <= 0 and BENCHMARK_FRAMES <= 0:
        print("Benchmark disabled (--benchmark_seconds 0). Proceeding directly to segment distribution...")

        def start_without_benchmark():
            wait_for_video_info()
            adjust_segments(video_fps, video_duration, total_frames)

        threading.Thread(target=start_without_benchmark, daemon=True).start()
        return

    for cid in connected_cids:
        send_benchmark_to_client(cid)


def handle_abandoned_segment(client_id):
    """
    Called after a grace period if a disconnected client has not reconnected.
    Re-queues its in-progress segment and assigns it to an available active client.
    """
    time.sleep(RECONNECT_GRACE_SECONDS)
    with state_lock:
        cinfo = clients_info.get(client_id)
        if not cinfo or cinfo['connected'] or job_completed:
            return
        seg_id = cinfo.get('current_segment_id')
        if seg_id is None:
            return
        seg = segments.get(seg_id)
        if not seg or seg['status'] == 'completed':
            return

        print(f"Client {client_id} did not reconnect in time. Re-queuing segment #{seg_id}...")
        seg['status'] = 'pending'
        seg['assigned_to'] = None
        cinfo['current_segment_id'] = None
        cinfo['state'] = 'idle'
        if seg_id not in pending_retry_segments:
            pending_retry_segments.append(seg_id)

        # Find an idle connected client to take over the segment immediately
        for other_cid, other_info in clients_info.items():
            if other_info['connected'] and other_info['current_segment_id'] is None:
                assign_work_to_client(other_cid)
                break


@app.route('/')
def index():
    return "FFmpeg-Cluster SocketIO Server"


@app.route('/status')
def status():
    if not verify_key(get_request_key()):
        abort(401)
    with state_lock:
        return jsonify({
            'file': FILE_BASENAME,
            'output': FILE_NAME_OUTPUT,
            'video_fps': video_fps,
            'video_duration': video_duration,
            'total_frames': total_frames,
            'clients_connected': sum(1 for c in clients_info.values() if c['connected']),
            'segments_total': len(segments),
            'segments_completed': sum(1 for s in segments.values() if s['status'] == 'completed'),
            'all_segments_generated': all_segments_generated,
            'job_completed': job_completed
        })


@app.route('/files/<path:filename>', methods=['GET'])
def serve_file(filename):
    """Serves the input video file (supports files located in any folder)."""
    if not verify_key(get_request_key()):
        abort(401)
    if not os.path.isfile(FILE_PATH):
        abort(404)
    return send_from_directory(FILE_DIR, FILE_BASENAME)


@app.route('/stream/<path:filename>', methods=['GET'])
def serve_stream(filename):
    """Serves generated HLS playlists and segments when --streaming is enabled."""
    if not STREAMING:
        abort(404)
    stream_abs = os.path.abspath(STREAM_FOLDER)
    target = os.path.abspath(os.path.join(stream_abs, filename))
    if not target.startswith(stream_abs) or not os.path.isfile(target):
        abort(404)
    response = send_from_directory(stream_abs, filename)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@app.route('/upload', methods=['POST'])
def upload_file():
    """Handles encoded video segment uploads from clients, validates them, and assigns next work."""
    if not verify_key(get_request_key()):
        return "Unauthorized: Invalid key", 401

    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400

    client_id = request.form.get('client_id')
    seg_id_raw = request.form.get('segment_id')

    with state_lock:
        # Resolve logical client_id if the client sent its SocketIO sid
        if client_id in sid_to_client:
            client_id = sid_to_client[client_id]

        target_seg = None
        if seg_id_raw is not None and str(seg_id_raw).isdigit():
            target_seg = segments.get(int(seg_id_raw))

        if target_seg is None and client_id in clients_info:
            curr_sid = clients_info[client_id].get('current_segment_id')
            if curr_sid is not None:
                target_seg = segments.get(curr_sid)

        if target_seg is None:
            # Fallback: look up any segment assigned to this client
            for s in segments.values():
                if s['assigned_to'] == client_id and s['status'] != 'completed':
                    target_seg = s
                    break

        if target_seg is None:
            return "Client or segment not recognized", 400

        save_path = os.path.join(UPLOAD_FOLDER, target_seg['filename'])

    file.save(save_path)

    # Validate uploaded segment to detect corrupted transfers or faulty encodes
    if not validate_segment_file(save_path):
        print(f"Warning: Uploaded segment #{target_seg['segment_id']} from {client_id} is corrupted or invalid!")
        if os.path.exists(save_path):
            os.remove(save_path)
        with state_lock:
            target_seg['retries'] += 1
            target_seg['status'] = 'pending'
            if target_seg['segment_id'] not in pending_retry_segments:
                pending_retry_segments.append(target_seg['segment_id'])
        if client_id in clients_info:
            assign_work_to_client(client_id)
        return "Corrupted segment file detected; scheduled for retry", 422

    with state_lock:
        target_seg['status'] = 'completed'
        if client_id in clients_info and clients_info[client_id].get('current_segment_id') == target_seg['segment_id']:
            clients_info[client_id]['current_segment_id'] = None
            clients_info[client_id]['state'] = 'idle'

    print(f"Segment #{target_seg['segment_id']} ({target_seg['filename']}) uploaded and verified from client {client_id}.")

    if STREAMING:
        threading.Thread(target=update_hls_stream, daemon=True).start()

    # Assign next segment if in dynamic segment_request/streaming mode or if retry segments are waiting
    assign_work_to_client(client_id)

    # Check if all segments are finished and ready to be combined
    check_and_combine_if_complete()

    return "File uploaded successfully", 200


@socketio.on('connect')
def handle_connect(auth=None):
    """Handles a new or reconnecting client connection."""
    provided_key = get_request_key(auth)
    if not verify_key(provided_key):
        print(f"Rejected client connection from {request.sid}: invalid key.")
        return False

    sid = request.sid
    requested_cid = None
    if isinstance(auth, dict):
        requested_cid = auth.get('client_id')
    if not requested_cid:
        requested_cid = request.args.get('client_id')

    logical_id = requested_cid or sid

    with state_lock:
        sid_to_client[sid] = logical_id
        if logical_id in clients_info:
            # Reconnecting client!
            cinfo = clients_info[logical_id]
            old_sid = cinfo.get('sid')
            if old_sid and old_sid in sid_to_client and old_sid != sid:
                del sid_to_client[old_sid]
            cinfo['sid'] = sid
            cinfo['connected'] = True
            cinfo['last_seen'] = time.time()
            print(f"Client {logical_id} reconnected (new sid={sid}).")
        else:
            clients_info[logical_id] = {
                'sid': sid,
                'connected': True,
                'fps': None,
                'current_segment_id': None,
                'last_seen': time.time(),
                'state': 'idle'
            }
            print(f"Client {logical_id} connected.")

        connected_count = sum(1 for c in clients_info.values() if c['connected'])

    if not benchmark_started and connected_count >= REQUIRED_CLIENTS:
        start_benchmark_for_all_clients()


@socketio.on('request_client_id')
def request_client_id(data=None):
    """
    Handles client registration / ID assignment and resumes interrupted work on reconnect.
    """
    sid = request.sid
    requested_cid = None
    client_key = None
    busy_segment_id = None

    if isinstance(data, dict):
        requested_cid = data.get('client_id')
        client_key = data.get('key')
        busy_segment_id = data.get('current_segment_id')

    if AUTH_KEY and client_key is not None and not verify_key(client_key):
        print(f"Client {sid} provided an invalid key in request_client_id.")
        disconnect()
        return

    with state_lock:
        current_logical = sid_to_client.get(sid, sid)
        if requested_cid and requested_cid != current_logical:
            # Merge into requested_cid record for seamless reconnect
            old_entry = clients_info.pop(current_logical, None)
            sid_to_client[sid] = requested_cid
            if requested_cid in clients_info:
                clients_info[requested_cid]['sid'] = sid
                clients_info[requested_cid]['connected'] = True
                clients_info[requested_cid]['last_seen'] = time.time()
            else:
                clients_info[requested_cid] = old_entry or {
                    'sid': sid,
                    'connected': True,
                    'fps': None,
                    'current_segment_id': None,
                    'last_seen': time.time(),
                    'state': 'idle'
                }
            logical_id = requested_cid
        else:
            logical_id = current_logical

        cinfo = clients_info[logical_id]
        socketio.emit('client_id', {'id': logical_id}, to=sid)

        # Resume last step if client reconnected during an active job
        if benchmark_started and not encoding_started and cinfo['fps'] is None:
            print(f"Resuming benchmark for reconnected client {logical_id}...")
            send_benchmark_to_client(logical_id)
        elif encoding_started and not job_completed:
            seg_id = cinfo.get('current_segment_id')
            if seg_id is not None and seg_id in segments and segments[seg_id]['status'] != 'completed':
                if busy_segment_id != seg_id:
                    print(f"Resending in-progress segment #{seg_id} to reconnected client {logical_id}...")
                    emit_segment_to_client(logical_id, segments[seg_id])
            elif cinfo.get('current_segment_id') is None:
                assign_work_to_client(logical_id)


@socketio.on('disconnect')
def handle_disconnect():
    """Handles client disconnection and schedules segment recovery if the client had active work."""
    sid = request.sid
    with state_lock:
        logical_id = sid_to_client.pop(sid, None)
        if not logical_id or logical_id not in clients_info:
            print("Client disconnected.")
            return
        cinfo = clients_info[logical_id]
        if cinfo.get('sid') == sid:
            cinfo['connected'] = False
            cinfo['last_seen'] = time.time()
            print(f"Client {logical_id} disconnected.")
            if cinfo.get('current_segment_id') is not None and not job_completed:
                threading.Thread(target=handle_abandoned_segment, args=(logical_id,), daemon=True).start()


@socketio.on('send_fps')
def handle_fps(data):
    """Handles benchmark FPS results sent by clients."""
    sid = request.sid
    with state_lock:
        logical_id = sid_to_client.get(sid, sid)
        fps = float(data.get('fps', 0.0))
        if logical_id in clients_info:
            clients_info[logical_id]['fps'] = fps
            clients_info[logical_id]['state'] = 'idle'
        print(f"Received benchmark FPS from client {logical_id}: {fps:.2f}")

        if encoding_started:
            # Late/reconnected client finished benchmark after encoding already started
            assign_work_to_client(logical_id)
            return

        connected_cids = [cid for cid, info in clients_info.items() if info['connected']]
        ready_cids = [cid for cid in connected_cids if clients_info[cid].get('fps') is not None]

        if len(ready_cids) >= len(connected_cids) and len(ready_cids) >= REQUIRED_CLIENTS:
            def launch_Adjust():
                wait_for_video_info()
                adjust_segments(video_fps, video_duration, total_frames)

            threading.Thread(target=launch_Adjust, daemon=True).start()


@socketio.on('finish')
def handle_client_finish(data):
    """Handles segment completion metrics sent by clients."""
    sid = request.sid
    with state_lock:
        logical_id = sid_to_client.get(sid, sid)
        fps = float(data.get('fps', 0.0)) if isinstance(data, dict) and data.get('fps') else None
        if fps and logical_id in clients_info:
            clients_info[logical_id]['fps'] = fps
        print(f"Client {logical_id} finished encoding segment (effective FPS: {fps}).")


@socketio.on('segment_error')
def handle_segment_error(data):
    """Handles error reports from clients when a segment encoding step fails, scheduling a retry."""
    sid = request.sid
    with state_lock:
        logical_id = sid_to_client.get(sid, sid)
        seg_id = data.get('segment_id') if isinstance(data, dict) else None
        err_msg = data.get('error', 'Unknown error') if isinstance(data, dict) else 'Unknown error'

        if seg_id is None and logical_id in clients_info:
            seg_id = clients_info[logical_id].get('current_segment_id')

        print(f"Client {logical_id} reported error on segment #{seg_id}: {err_msg}")
        if seg_id is not None and seg_id in segments:
            seg = segments[seg_id]
            if seg['status'] != 'completed':
                seg['retries'] += 1
                seg['status'] = 'pending'
                seg['assigned_to'] = None
                if seg_id not in pending_retry_segments:
                    pending_retry_segments.append(seg_id)

        if logical_id in clients_info:
            clients_info[logical_id]['current_segment_id'] = None
            clients_info[logical_id]['state'] = 'idle'

    if seg_id is not None and seg_id in segments and segments[seg_id]['retries'] <= MAX_SEGMENT_RETRIES:
        assign_work_to_client(logical_id)
    elif seg_id is not None:
        print(f"Segment #{seg_id} exceeded max retries ({MAX_SEGMENT_RETRIES}) on client {logical_id}.")


def init_from_args(parsed_args):
    """Initializes server globals from parsed CLI arguments."""
    global REQUIRED_CLIENTS, BENCHMARK_SECONDS, BENCHMARK_FRAMES
    global FILE_PATH, FILE_DIR, FILE_BASENAME, FILE_NAME_OUTPUT
    global PORT, EXACTLY, STREAMING, STREAMING_DELAY, STREAMING_FFMPEG_PARAMS
    global SEGMENT_TIME, SEGMENT_TIME_FOR_CLIENT, SEGMENT_REQUEST, AUTH_KEY
    global FFMPEG_PARAMS, VIDEO_FFMPEG_PARAMS, AUDIO_FFMPEG_PARAMS

    REQUIRED_CLIENTS = parsed_args.required_clients
    BENCHMARK_SECONDS = parsed_args.benchmark_seconds
    BENCHMARK_FRAMES = parsed_args.benchmark_frames

    FILE_PATH = os.path.abspath(parsed_args.file_name)
    FILE_DIR = os.path.dirname(FILE_PATH) or '.'
    FILE_BASENAME = os.path.basename(FILE_PATH)
    FILE_NAME_OUTPUT = parsed_args.file_name_output

    PORT = parsed_args.port
    EXACTLY = parsed_args.exactly
    STREAMING = parsed_args.streaming
    STREAMING_DELAY = parsed_args.streaming_delay
    STREAMING_FFMPEG_PARAMS = shlex.split(parsed_args.streamingffmpeg_params)
    SEGMENT_TIME = parsed_args.segment_time
    SEGMENT_TIME_FOR_CLIENT = parsed_args.segment_time_for_client
    SEGMENT_REQUEST = parsed_args.segment_request
    AUTH_KEY = parsed_args.key or ''

    VIDEO_FFMPEG_PARAMS, AUDIO_FFMPEG_PARAMS = split_ffmpeg_params(parsed_args.ffmpeg_params)
    FFMPEG_PARAMS = VIDEO_FFMPEG_PARAMS

    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    if STREAMING:
        os.makedirs(STREAM_FOLDER, exist_ok=True)


if __name__ == '__main__':
    parser = build_parser()
    args = parser.parse_args()
    init_from_args(args)

    if not os.path.isfile(FILE_PATH):
        raise FileNotFoundError(f"Input video file not found: '{FILE_PATH}'")

    print(f"Server is running on http://0.0.0.0:{PORT}")
    print(f"Serving input file: {FILE_PATH}")
    print(f"Video FFmpeg params: {' '.join(VIDEO_FFMPEG_PARAMS)}")
    if AUDIO_FFMPEG_PARAMS:
        print(f"Audio FFmpeg params (applied on server): {' '.join(AUDIO_FFMPEG_PARAMS)}")
    if SEGMENT_REQUEST or STREAMING:
        print(f"Dynamic segment mode active (segment_request={SEGMENT_REQUEST}, streaming={STREAMING}, segment_time={SEGMENT_TIME})")

    socketio.run(app, host='0.0.0.0', port=PORT, allow_unsafe_werkzeug=True)
