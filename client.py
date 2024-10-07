import socketio
import subprocess
import argparse
import re
import requests
import os

# Argumente parsen
parser = argparse.ArgumentParser(description='Start the SocketIO client.')
parser.add_argument('--server_ip', type=str, default='localhost',
                    help='IP address of the SocketIO server (default: localhost).')
parser.add_argument('--server_port', type=int, default=5000,
                    help='Port of the SocketIO server (default: 5000).')
args = parser.parse_args()

sio = socketio.Client()

client_id = None  # Variable für die Client-ID

@sio.event
def connect():
    global client_id
    print("Connected to server.")
    sio.emit('request_client_id')

@sio.event
def client_id(data):
    global client_id
    client_id = data['id']  # Speichere die empfangene Client-ID
    print(f"Received Client ID: {client_id}")

def send_file_to_server(file_path):
    """Sende die fertige Datei an den Server."""
    url = f"http://{args.server_ip}:{args.server_port}/upload"
    files = {'file': open(file_path, 'rb')}
    data = {'client_id': client_id}  # Verwende die gespeicherte Client-ID
    try:
        response = requests.post(url, files=files, data=data)
        if response.status_code == 200:
            os.remove(file_path)
            print(f"File {file_path} successfully uploaded to the server.")
        else:
            print(f"Failed to upload file {file_path} to the server.")
    except Exception as e:
        print(f"Error uploading file: {e}")

@sio.event
def start_benchmark(data):
    file_url = f"http://{args.server_ip}:{args.server_port}{data['file_url']}"
    params = data['params']
    print(f"Starting benchmark with FFmpeg parameters: {params}")

    # Erstelle den FFmpeg-Befehl ohne eine Ausgabedatei
    ffmpeg_command = [
        'ffmpeg',
        '-i', file_url,
        *params.split(),
        '-t', '10',  # Setze die Dauer auf 10 Sekunden
        '-f', 'null',  # Keine Ausgabedatei, benutze null als Format
        '/dev/null'  # Für Unix, für Windows benutze 'NUL'
    ]

    # Führe den FFmpeg-Befehl aus und erfasse die Ausgabe
    try:
        result = subprocess.run(ffmpeg_command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = result.stderr.decode()
        print("FFmpeg command executed successfully.")
        print("FFmpeg Output:")
        print(output)  # Geben Sie die gesamte FFmpeg-Ausgabe aus

        # Analysiere die FFmpeg-Ausgabe nach FPS und Speed
        fps_match = re.search(r'(\d+(?:\.\d+)?) fps', output)  # Regex für FPS
        speed_match = re.findall(r'speed=(\d+(?:\.\d+)?)x', output)  # Alle Vorkommen von Speed
        
        if fps_match and speed_match:
            fps = float(fps_match.group(1))
            speed = float(speed_match[-1])  # Nimm den letzten gefundenen Speed-Wert

            print(f"Extracted FPS: {fps}")
            print(f"Extracted Speed: {speed}x")

            # Berechnung der effektiven FPS
            effective_fps = fps * speed
            print(f"Effective FPS during processing: {effective_fps}")

            # Sende die effektive FPS an den Server
            if effective_fps > 0:
                sio.emit('send_fps', {'fps': effective_fps})
            else:
                print("Effective FPS extraction returned 0. Skipping sending to server.")
        else:
            print("FPS or Speed not found in FFmpeg output.")

    except subprocess.CalledProcessError as e:
        print(f"An error occurred while running FFmpeg: {e}")

@sio.event
def adjust_segment(data):
    start_time = data['start_time']
    end_time = data['end_time']
    file_url = f"http://{args.server_ip}:{args.server_port}{data['file_url']}"
    params = data['params']
    segment_duration = end_time - start_time

    print(f"Adjust segment: Start at {start_time} seconds, End at {end_time}, duration {segment_duration} seconds.")

    output_file = 'segment_output.mp4'

    # Erstelle den FFmpeg-Befehl mit spezifischem Start und Segmentdauer
    ffmpeg_command = [
        'ffmpeg',
        '-ss', str(start_time),  # Startzeit
        '-to', str(end_time),
        '-i', file_url,
        *params.split(),
        output_file
    ]

    # Führe den FFmpeg-Befehl aus
    try:
        result = subprocess.run(ffmpeg_command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = result.stderr.decode()
        print("FFmpeg command executed successfully.")
        print("FFmpeg Output:")
        print(output)

        # FPS und Speed extrahieren und an den Server senden (wie zuvor)
        fps_match = re.search(r'(\d+(?:\.\d+)?) fps', output)
        speed_match = re.findall(r'speed=(\d+(?:\.\d+)?)x', output)
        
        if fps_match and speed_match:
            fps = float(fps_match.group(1))
            speed = float(speed_match[-1])
            effective_fps = fps * speed
            print(f"Effective FPS: {effective_fps}")
            sio.emit('finish', {'fps': effective_fps})
            send_file_to_server(output_file)
        else:
            print("FPS or Speed not found in FFmpeg output.")

    except subprocess.CalledProcessError as e:
        print(f"An error occurred while running FFmpeg: {e}")


@sio.event
def disconnect():
    print("Disconnected from server.")

if __name__ == '__main__':
    # Verbinde zum Server
    server_url = f'http://{args.server_ip}:{args.server_port}'
    print(f"Connecting to server at {server_url}...")
    sio.connect(server_url)
    sio.wait()

