from flask import Flask, send_from_directory, abort, request
from flask_socketio import SocketIO
import argparse
import os
import eventlet
import numpy as np
import subprocess

# Erstelle die Flask-App und die SocketIO-Instanz
#app = Flask(__name__)
#sio = SocketIO(app, cors_allowed_origins='*')  # Erstelle die SocketIO-Instanz

# Argumente parsen
parser = argparse.ArgumentParser(description='Start the Flask-SocketIO server.')
parser.add_argument('--required_clients', type=int, default=3,
                    help='Number of clients required to start the benchmark (default: 3).')
parser.add_argument('--benchmark_seconds', type=int, default=10,
                    help='Number of seconds from the video to benchmark (default: 10).')
parser.add_argument('--file_name', type=str, required=True,
                    help='Name of the file to be served over HTTP.')
parser.add_argument('--ffmpeg_params', type=str, default='-c:v libx264 -preset fast',
                    help='FFmpeg encoding parameters (default: "-c:v libx264 -preset fast").')
parser.add_argument('--file_name_output', type=str, default='output.mp4',
                    help='Name of the file (default: output.mp4)')
parser.add_argument('--port', type=int, default=5000,
                    help='Port to run the Flask-SocketIO server (default: 5000).')
parser.add_argument('--exactly', type=lambda x: (str(x).lower() in ['true', '1', 'yes']), default=True,
                    help='Use exact frame counting method (default: True). Set to False for approximate counting.')
parser.add_argument('--streaming', 
                    type=bool, 
                    default=False, 
                    help='For live streaming (default: False).')
parser.add_argument('--streaming_delay', 
                    type=int, 
                    default=30, 
                    help='The delay for the livestream ensures that there are no interruptions. Depending on the encoder speed, this should be adjusted (default: 30).')
parser.add_argument('--streamingffmpeg_params', 
                    type=str, 
                    default='-hls_time 6 -hls_flags delete_segments -hls_playlist_type event -hls_segment_type fmp4 -hls_fmp4_init_filename init.mp4 -master_pl_name master.m3u8 output.m3u8',
                    help='Streaming FFmpeg encoding parameters (default: "-hls_time 6 -hls_flags delete_segments -hls_playlist_type event -hls_segment_type fmp4 -hls_fmp4_init_filename init.mp4 -master_pl_name master.m3u8 output.m3u8").')
parser.add_argument('--segment_time',
                    type=int, 
                    help='Specify the duration of each segment in seconds.')
parser.add_argument('--segment_time_for_client', 
                    type=int, 
                    default=10, 
                    help='Use this option if the benchmark is off (default: 10).')
parser.add_argument('--segment_request', 
                    type=bool, 
                    default=False, 
                    help='Handle requests from clients for a mode where the segments are requested, and the server delivers them to the clients (default: False).')
parser.add_argument('--key', 
                    type=str,
                    default='', 
                    help='Passwort für die Encode-Verbindung (Standard: keine).')

args = parser.parse_args()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app)
clients = []
client_fps = {}
client_filenames = {}

REQUIRED_CLIENTS = args.required_clients  # Anzahl der benötigten Clients
FFMPEG_PARAMS = args.ffmpeg_params.split()  # FFmpeg Parameter
#FFMPEG_PARAMS = args.ffmpeg_params  # FFmpeg Parameter
FILE_NAME = args.file_name  # Name der Datei
FILE_NAME_OUTPUT = args.file_name_output  # Name der Datei
PORT = args.port  # Port für den Server
EXACTLY=args.exactly
BENCHMARK_SECONDS=args.benchmark_seconds
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Globale Variablen für FPS und Videodauer
video_fps = None
video_duration = None
total_frames = None

@app.route('/')
def index():
    return "SocketIO Server"

@socketio.on('request_client_id')
def request_client_id():
    """Sendet die Client-ID zurück."""
    client_id = request.sid  # Holen Sie sich die Client-ID
    print(f"Client {client_id} connected.")
    socketio.emit('client_id', {'id': client_id}, room=client_id)  # Sende die Client-ID zurück

@app.route('/upload', methods=['POST'])
def upload_file():
    """Handle file upload from clients."""
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400

    client_id = request.form.get('client_id')  # Client-ID aus dem POST-Request holen
    if client_id not in client_filenames:
        return "Client not recognized", 400

    # Speichern der Datei im uploads-Verzeichnis
    save_path = os.path.join(UPLOAD_FOLDER, client_filenames[client_id])
    file.save(save_path)

    # Überprüfe, ob alle erforderlichen Dateien hochgeladen wurden
    if all(filename in os.listdir(UPLOAD_FOLDER) for filename in client_filenames.values()):
        print("All files uploaded, starting to combine segments.")
        segment_files = [os.path.join(UPLOAD_FOLDER, filename) for filename in client_filenames.values()]
        combine_segments(segment_files)  # Gebe den gewünschten Ausgabedateinamen an.

    return "File uploaded successfully", 200

@app.route('/files/<path:filename>', methods=['GET'])
def serve_file(filename):
    """Serve a file from the specified directory."""
    file_path = os.path.join('.', FILE_NAME)
    if not os.path.isfile(file_path):
        abort(404)
    return send_from_directory('.', FILE_NAME)

@socketio.on('connect')
def handle_connect():
    """Handle a new client connection."""
    print("Client connected.")
    clients.append(request.sid)
    if len(clients) >= REQUIRED_CLIENTS:
        start_benchmark_for_all_clients()

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    print("Client disconnected.")
    clients.remove(request.sid)
    if request.sid in client_fps:
        del client_fps[request.sid]

@socketio.on('send_fps')
def handle_fps(data):
    """Handle FPS results sent by clients."""
    client_id = request.sid
    fps = data['fps']
    client_fps[client_id] = fps
    print(f"Received FPS from client {client_id}: {fps}")

    # Wenn alle FPS empfangen sind, vergleichen und Segmente berechnen
    if len(client_fps) == len(clients):
        adjust_segments()

@socketio.on('finish')
def handle_client_finish(data):
    """Handle FPS results sent by clients."""
    client_id = request.sid
    fps = data['fps']
    client_fps[client_id] = fps
    print(f"Received FPS from client {client_id}: {fps}")

    # Wenn alle FPS empfangen sind, vergleichen und Segmente berechnen
    if len(client_fps) == len(clients):
        print(f"{client_id} finish")

def get_video_info(video_file, exactly=False):
    """ Use ffprobe to extract video duration, FPS, and total frame count. If exactly is True, return the exact frame count. """
    print (f"Use ffprobe to extract video information from {video_file}")
    if exactly:
        # ffprobe-Befehl zur Ermittlung von FPS, Dauer und exakter Frame-Anzahl
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-count_frames',
            '-show_entries', 'stream=r_frame_rate,duration,nb_read_frames',
            '-of', 'default=noprint_wrappers=1:nokey=1', 
            video_file
        ]
    else:
        # Standard ffprobe-Befehl zur Ermittlung von FPS und Dauer
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=r_frame_rate,duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', 
            video_file
        ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output = result.stdout.split('\n')

    # Extrahiere FPS (z.B. '25/1')
    frame_rate = output[0]
    fps = eval(frame_rate)  # Wandelt den Bruch in eine Float-Zahl um

    if exactly:
        # Extrahiere die exakte Anzahl an Frames
        total_frames = int(output[2])
        duration = float(output[1])  # Dauer des Videos in Sekunden
        return fps, duration, total_frames
    else:
        # Extrahiere Dauer und berechne die geschätzte Anzahl an Frames
        duration = float(output[1])  # Dauer des Videos in Sekunden
        total_frames = math.ceil(fps * duration)  # Schätz die Anzahl der Frames
        return fps, duration, total_frames

def wait_for_video_info():
    """Warte, bis die globalen Variablen einen Wert haben."""
    global video_fps, video_duration, total_frames
    while video_fps is None or video_duration is None or total_frames is None:
        print("Warte auf Videoinformationen...")
        time.sleep(1)  # Warte 1 Sekunde, bevor die Prüfung erneut erfolgt

def adjust_segments():
    """
    Adjust encoding segments based on the FPS results of each client.
    """
    #global video_fps, video_duration, total_frames
    
    # Warte auf Videoinformationen, bevor die Berechnungen durchgeführt werden
    #wait_for_video_info()

    # Holen der FPS und Videodauer
    video_fps, video_duration, total_frames = get_video_info(FILE_NAME, EXACTLY)
    
    fps_values = list(client_fps.values())
    total_fps_capacity = np.sum(fps_values)

    encode_speed = total_fps_capacity / video_fps

    encoding_time = total_frames / total_fps_capacity
    
    print(f"Video FPS: {video_fps}")
    print(f"Video duration: {video_duration} seconds")
    print(f"Total frames: {total_frames}")
    print(f"Total FPS capacity: {total_fps_capacity}")
    print(f"Estimated encoding time: {encoding_time:.2f} seconds speed x{encode_speed:.2f}")

    current_time = 0  # Startzeitpunkt für den ersten Client
    current_frame = 0  # Startzeitpunkt für den ersten Client
 
    segment_count = 0

    # Berechnung des Prozentsatzes, den jeder Client zur Gesamtleistung beiträgt
    for client in clients:
        client_capacity_percentage = client_fps[client] / total_fps_capacity  # Anteil des Clients
        client_duration = client_capacity_percentage * video_duration  # Wie viel Videodauer dieser Client verarbeiten soll
        client_frame_share = client_duration * total_frames  # Wie viele Frames dieser Client verarbeiten soll

        client_capacity_percentage = client_fps[client] / total_fps_capacity  # Anteil des Clients
        client_frame_share = int(client_capacity_percentage * total_frames)  # Wie viele Frames dieser Client verarbeiten soll

        start_time = current_time  # Startzeitpunkt des aktuellen Clients
        end_time = current_time + client_duration  # Endzeitpunkt des aktuellen Clients
        current_time = end_time  # Update des Startzeitpunkts für den nächsten Client
        
        start_frame = current_frame  # Startframe des aktuellen Clients
        end_frame = current_frame + client_frame_share  # Endframe des aktuellen Clients
        current_frame = end_frame + 1  # Update des Startframes für den nächsten Client, um Überlappungen zu vermeiden

        print(f"Client {client} should encode from frame {start_frame} to frame {end_frame} {client_frame_share:.2f} frames.")

        client_filenames[client] = f"segment_{client}_{segment_count}.mp4"
        segment_count+=1
        print(f"Client {client} should encode from {start_time:.2f} to {end_time:.2f} seconds client_duration: {client_duration:.2f} as {client_filenames[client]}.")

        file_url = f'/files/{FILE_NAME}'

        if EXACTLY:
            params = FFMPEG_PARAMS + [
                '-vf', fr"select=between(n\,{start_frame}\,{end_frame})",
                '-vsync', 'vfr',
                '-an'
            ]
            socketio.emit('adjust_segment', {'file_url': file_url, 'params': params}, to=client)
        else:
            params = FFMPEG_PARAMS + ['-ss', 'start_time','-to', 'end_time']
            socketio.emit('adjust_segment', {'file_url': file_url, 'params': params}, to=client)

def combine_segments(segment_files):
    output_file = FILE_NAME_OUTPUT
    """Combine multiple video segments into one."""
    with open('filelist.txt', 'w') as f:
        for segment in segment_files:
            f.write(f"file '{segment}'\n")

    # Führe den FFmpeg-Befehl aus, um die Segmente zu kombinieren
    if EXACTLY:
        ffmpeg_command = [
            'ffmpeg',
            '-i', FILE_NAME,  # Audio von input.mp4 nutzen
            '-f', 'concat',
            '-safe', '0',
            '-i', 'filelist.txt',  # Segmente kombinieren
            '-c:v', 'copy',  # Video-Stream ohne Neucodierung
            '-c:a', 'copy',  # Audio ohne Neucodierung kopieren. Eine Funktion, die prüft, ob in den FFmpeg-Parametern eine Umkonvertierung gewünscht war, ist noch nötig.
            '-map', '0:a',  # Audio von input.mp4 mappen
            '-map', '1:v',  # Video von den Segmenten mappen
            output_file
        ]
    else:
        ffmpeg_command = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', 'filelist.txt',
            '-c', 'copy',  # Keine Neucodierung, nur zusammenfügen
            output_file
        ]

    try:
        result = subprocess.run(ffmpeg_command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"Combined segments into {output_file}.")
        
        # Dateien nach dem erfolgreichen Kombinieren löschen
        for segment in segment_files:
            os.remove(segment)
            print(f"Deleted segment file: {segment}")
        
        os.remove('filelist.txt')  # Löschen der Dateiliste
        print("Deleted filelist.txt.")
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while combining segments: {e}")

def start_benchmark_for_all_clients():
    """Start the benchmark for all connected clients."""
    global video_fps, video_duration, total_frames
    for client in clients:
        print(f"Sending start_benchmark to client {client}")
        file_url = f'/files/{FILE_NAME}'
        #params = list(FFMPEG_PARAMS.split())  # Convert string to list
        #params.extend(['-t', '10'])  # Extend the list
        #params = FFMPEG_PARAMS + f" -t {10}"
        params = FFMPEG_PARAMS + ['-t', '10']
        socketio.emit('start_benchmark', {'file_url': file_url, 'params': params}, to=client)
        print(f"Sent start_benchmark to {client}")

    # Holen der FPS und Videodauer
    #video_fps, video_duration, total_frames = get_video_info(FILE_NAME, EXACTLY)

if __name__ == '__main__':
    print(f"Server is running on http://0.0.0.0:{PORT}")
    socketio.run(app, host='0.0.0.0', port=PORT)

