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
parser.add_argument('--file_name', type=str, required=True,
                    help='Name of the file to be served over HTTP.')
parser.add_argument('--ffmpeg_params', type=str, default='-c:v libx264 -preset fast',
                    help='FFmpeg encoding parameters (default: "-c:v libx264 -preset fast").')
parser.add_argument('--file_name_output', type=str, default='output.mp4',
                    help='Name of the file (default: output.mp4)')
parser.add_argument('--port', type=int, default=5000,
                    help='Port to run the Flask-SocketIO server (default: 5000).')
args = parser.parse_args()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app)
clients = []
client_fps = {}
client_filenames = {}

REQUIRED_CLIENTS = args.required_clients  # Anzahl der benötigten Clients
FFMPEG_PARAMS = args.ffmpeg_params  # FFmpeg Parameter
FILE_NAME = args.file_name  # Name der Datei
FILE_NAME_OUTPUT = args.file_name_output  # Name der Datei
PORT = args.port  # Port für den Server
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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
    return send_from_directory('.', filename)

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

def get_video_info(video_file):
    """ Use ffprobe to extract video duration and FPS. """
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=r_frame_rate,duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', 
        video_file
    ]
    
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output = result.stdout.split('\n')
    
    # Extrahiere FPS und Dauer
    frame_rate = output[0]  # FPS-Wert als Bruch (z.B. '25/1')
    duration = float(output[1])  # Länge des Videos in Sekunden

    # FPS als float berechnen
    fps = eval(frame_rate)  # eval('25/1') -> 25.0

    return fps, duration


def adjust_segments():
    """
    Adjust encoding segments based on the FPS results of each client.
    """
    # Holen der FPS und Videodauer
    video_fps, video_duration = get_video_info(FILE_NAME)

    # Berechne die Gesamtanzahl an Frames im Video
    total_frames = int(video_fps * video_duration)
    
    fps_values = list(client_fps.values())
    total_fps_capacity = np.sum(fps_values)
    
    print(f"Video FPS: {video_fps}")
    print(f"Video duration: {video_duration} seconds")
    print(f"Total frames: {total_frames}")
    print(f"Total FPS capacity: {total_fps_capacity}")

    current_time = 0  # Startzeitpunkt für den ersten Client
    
    segment_count = 0

    # Berechnung des Prozentsatzes, den jeder Client zur Gesamtleistung beiträgt
    for client in clients:
        client_capacity_percentage = client_fps[client] / total_fps_capacity  # Anteil des Clients
        client_duration = client_capacity_percentage * video_duration  # Wie viel Videodauer dieser Client verarbeiten soll
        client_capacity_percentage = client_fps[client] / total_fps_capacity  # Anteil des Clients
        client_frame_share = client_duration * total_frames  # Wie viele Frames dieser Client verarbeiten soll
        segment_time = client_duration / video_fps  # Segmentgröße in Sekunden
        
        print(f"Client {client} should encode {client_frame_share:.2f} frames.")
        print(f"Client {client} will process for {segment_time:.2f} seconds.")

        start_time = current_time  # Startzeitpunkt des aktuellen Clients
        end_time = current_time + client_duration  # Endzeitpunkt des aktuellen Clients
        current_time = end_time  # Update des Startzeitpunkts für den nächsten Client
        
        print(f"Client {client} should encode from {start_time:.2f} to {end_time:.2f} seconds.")
        
        client_filenames[client] = f"segment_{client}_{segment_count}.mp4"
        segment_count+=1
        print(f"Client {client} should encode from {start_time:.2f} to {end_time:.2f} seconds as {client_filenames[client]}.")
        

        file_url = f'/files/{FILE_NAME}'
        socketio.emit('adjust_segment', {'start_time': start_time, 'end_time': end_time, 'file_url': file_url, 'params': FFMPEG_PARAMS}, to=client)

def combine_segments(segment_files):
    output_file = FILE_NAME_OUTPUT
    """Combine multiple video segments into one."""
    with open('filelist.txt', 'w') as f:
        for segment in segment_files:
            f.write(f"file '{segment}'\n")

    # Führe den FFmpeg-Befehl aus, um die Segmente zu kombinieren
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
    for client in clients:
        print(f"Sending start_benchmark to client {client}")
        file_url = f'/files/{FILE_NAME}'
        socketio.emit('start_benchmark', {'file_url': file_url, 'params': FFMPEG_PARAMS}, to=client)
        print(f"Sent start_benchmark to {client}")

if __name__ == '__main__':
    print(f"Server is running on http://0.0.0.0:{PORT}")
    socketio.run(app, host='0.0.0.0', port=PORT)

