### English Version:

# FFmpeg-Cluster
FFmpeg Cluster is a distributed system designed for efficient video processing. It allows multiple clients to collaborate in encoding video segments, which are then combined into a single output file. This project uses Flask-SocketIO for communication between the server and clients and FFmpeg for video processing.

## Usage:

Start the server and specify the number of clients with `--required_clients` that should work on your video. The video file must be in the same directory as the server. You can pass encoding parameters with `--ffmpeg_params` as shown in the example, and set the output filename with `--file_name_output`.

### Requirements:
- [FFmpeg](https://github.com/btbn/ffmpeg-builds/releases)
- Flask
- Flask-SocketIO
- eventlet
- numpy
- [requirements.txt](https://raw.githubusercontent.com/JanisPlayer/FFmpeg-Cluster/refs/heads/main/requirements.txt)

```bash
python3 server.py --required_clients 2 --file_name input.mp4 --ffmpeg_params "-c:v libsvtav1 -preset 6 -crf 30"
```

Clients can easily connect to the server, and the port can be adjusted optionally. It is strongly advised not to use this unfinished project without a firewall.

```bash
python client.py --server_ip 192.168.0.86
```

Once all clients are connected, a benchmark is started on all clients. Based on the results, it is calculated how much of the video each client should encode. The segments are then sent back to the server and combined. In the current version, changing the audio encoder settings is not possible, as in "exactly" mode, the original video audio is used when combining segments.

The encoding efficiency compared to encoding on a single machine may vary depending on how many segments are combined, as B-frames may be copied from other segments. However, the efficiency was satisfactory for me, especially since so much time can be saved. It is even possible to install FFmpeg on a smartphone with Userland or Termux and use it for processing.

A live-streaming feature is still in development, so these parameters are currently not usable.

**Known Issues:**
The server may freeze, or clients might lose connection. If the host running the server becomes overloaded, it can also lead to freezing, which results in corrupted data being sent or received. This issue may be related to threading problems, and a potential fix could be running the server in a mode that handles threads more effectively. These issues tend to occur more frequently with longer videos and encoding times.

A reconnect solution needs to be implemented, where the client informs the server about its identity and then returns to the last processing step. Faulty steps should be detected and retried.

Videos with a high number of frames can take a long time to process with FFprobe. One way to improve this for videos without a variable framerate is to set the `--exactly` parameter to `False`. This will estimate the frame count based on time and FPS data. To make this estimation more accurate, a check could be performed to see where the next frame appears or if the end of the video matches the estimated frame count. Alternatively, at the very end of the video, time instead of frame count could be used to pass data to the client, preventing duplicate frames. Currently, frame estimates are passed in frame counts rather than time intervals, which is why the audio parameter has no effect at the moment.


### Server Options:
```bash
options:
  -h, --help            Show this help message and exit.
  --required_clients REQUIRED_CLIENTS
                        Number of clients required to start the benchmark (default: 3).
  --benchmark_seconds BENCHMARK_SECONDS
                        Number of seconds from the video to benchmark (default: 10).
  --file_name FILE_NAME
                        Name of the file to be served over HTTP.
  --ffmpeg_params FFMPEG_PARAMS
                        FFmpeg encoding parameters (default: "-c:v libx264 -preset fast").
  --file_name_output FILE_NAME_OUTPUT
                        Name of the output file (default: output.mp4).
  --port PORT           Port to run the Flask-SocketIO server (default: 5000).
  --exactly EXACTLY     Use exact frame counting method (default: True). Set to False for approximate counting.
```

### Features that still need to be added or are not yet usable:
```bash
  --streaming STREAMING
                        For live streaming (default: False).
  --streaming_delay STREAMING_DELAY
                        The delay for the livestream ensures that there are no interruptions. Depending on the encoder speed, this should be adjusted (default: 30).
  --streamingffmpeg_params STREAMINGFFMPEG_PARAMS
                        Streaming FFmpeg encoding parameters (default: "-hls_time 6 -hls_flags delete_segments -hls_playlist_type event -hls_segment_type fmp4 -hls_fmp4_init_filename init.mp4 -master_pl_name master.m3u8 output.m3u8").
  --segment_time SEGMENT_TIME
                        Specify the duration of each segment in seconds.
  --segment_time_for_client SEGMENT_TIME_FOR_CLIENT
                        Use this option if the benchmark is off (default: 10).
  --segment_request SEGMENT_REQUEST
                        Handle requests from clients for a mode where the segments are requested, and the server delivers them to the clients (default: False).
  --key KEY             Password for the encoding connection (default: none).
```

### Client Options:
```bash
options:
  -h, --help            Show this help message and exit.
  --server_ip SERVER_IP
                        IP address of the SocketIO server (default: localhost).
  --server_port SERVER_PORT
                        Port of the SocketIO server (default: 5000).
```

---

### Deutsche Version:

# FFmpeg-Cluster
FFmpeg-Cluster ist ein verteiltes System, das für effiziente Videobearbeitung entwickelt wurde. Es ermöglicht mehreren Clients, zusammen an der Kodierung von Videosegmenten zu arbeiten, die dann zu einer einzigen Ausgabedatei kombiniert werden. Dieses Projekt verwendet Flask-SocketIO für die Kommunikation zwischen dem Server und den Clients sowie FFmpeg für die Videobearbeitung.

## Verwendung:

Starte den Server und wähle mit `--required_clients` aus, wie viele Clients an deinem Video arbeiten sollen. Die Videodatei muss sich aktuell im gleichen Verzeichnis befinden. Danach können mit `--ffmpeg_params` die Parameter übergeben werden, wie im Beispiel gezeigt. Der Ausgabename kann mit `--file_name_output` festgelegt werden.

### Erforderlich:
- [FFmpeg](https://github.com/btbn/ffmpeg-builds/releases)
- Flask
- Flask-SocketIO
- eventlet
- numpy
- [requirements.txt](https://raw.githubusercontent.com/JanisPlayer/FFmpeg-Cluster/refs/heads/main/requirements.txt)

```bash
python3 server.py --required_clients 2 --file_name input.mp4 --ffmpeg_params "-c:v libsvtav1 -preset 6 -crf 30"
```

Clients können einfach mit dem Server verbunden werden, der Port kann optional angepasst werden. Es wird dringend davon abgeraten, dieses unfertige Projekt ohne Firewall zu verwenden.

```bash
python client.py --server_ip 192.168.0.86
```

Sind alle Clients verbunden, wird ein Benchmark auf allen Clients gestartet. Danach werden die Ergebnisse berechnet, und es wird entschieden, welcher Client wie viel vom Video encodiert. Am Ende werden die Segmente an den Server zurückgesendet und zusammengefügt. In der aktuellen Version ist das Ändern der Audio-Encoder-Einstellungen nicht möglich, da im "exactly"-Modus das Audio des Originalvideos später beim Kombinieren verwendet wird.

Der Unterschied in der Kodierungseffizienz auf einem einzelnen Rechner kann je nach Anzahl der zu kombinierenden Segmente variieren, da die B-Frames beim Kopieren auch von anderen Segmenten übernommen werden. Die Effizienz war jedoch für mich zufriedenstellend, insbesondere da so viel Zeit gespart werden kann. Es wäre sogar möglich, auf einem Smartphone mit Userland oder Termux FFmpeg zu installieren und dieses für die Berechnung zu nutzen.

Eine Livestream-Funktion ist noch in Planung, daher sind diese Parameter derzeit nicht nutzbar.

**Bekannte Probleme:**
Es kann vorkommen, dass der Server einfriert oder Clients die Verbindung verlieren. Wenn der Host, auf dem der Server läuft, stark belastet wird, kann dies ebenfalls zu einem Einfrieren führen. Das hat zur Folge, dass Daten fehlerhaft gesendet oder empfangen werden. Dieses Problem könnte auch durch Thread-Management-Fehler verursacht werden. Eine mögliche Lösung wäre es, den Server in einem Modus zu starten, der besser mit Threads umgehen kann. Diese Probleme treten häufiger auf, je länger das Video ist und je länger der Encoding-Prozess dauert.

Es muss noch eine Reconnect-Lösung entwickelt werden, bei der der Client dem Server mitteilt, wer er ist, und dann an den letzten Verarbeitungsschritt zurückkehrt. Fehlerhafte Schritte sollten erkannt und wiederholt werden.

Videos mit vielen Frames können bei der Nutzung von FFprobe zu längeren Verarbeitungszeiten führen. Eine Möglichkeit, dies zu beschleunigen, besteht darin, bei Videos ohne variable Framerate den Parameter `--exactly` auf `False` zu setzen. Dadurch wird die Anzahl der Frames anhand der Zeit und der FPS-Daten geschätzt. Um diese Schätzung zu präzisieren, könnte man prüfen, an welcher Stelle das nächste Frame erscheint oder ob das Ende des Videos mit der geschätzten Framezahl übereinstimmt. Alternativ könnte am Ende des Videos die Zeit statt der Framezahl verwendet werden, um dies an den Client zu übermitteln und doppelte Frames zu vermeiden. Derzeit wird die Schätzung in Frames und nicht in Zeitabständen vorgenommen, weshalb der Audio-Parameter keinen Einfluss hat.

### Server-Optionen:
```bash
options:
  -h, --help            Zeigt diese Hilfe und beendet das Programm.
  --required_clients REQUIRED_CLIENTS
                        Anzahl der Clients, die für den Benchmark benötigt werden (Standard: 3).
  --benchmark_seconds BENCHMARK_SECONDS
                        Dauer des Benchmarks in Sekunden (Standard: 10).
  --file_name FILE_NAME
                        Name der Datei, die über HTTP bereitgestellt wird.
  --ffmpeg_params FFMPEG_PARAMS
                        FFmpeg-Kodierungsparameter (Standard: "-c:v libx264 -preset fast").
  --file_name_output FILE_NAME_OUTPUT
                        Name der Ausgabedatei (Standard: output.mp4).
  --port PORT           Port für den Flask-SocketIO-Server (Standard: 5000).
  --exactly EXACTLY     Verwendet die exakte Frame-Zählmethode (Standard: True). Auf False setzen, um ungefähres Zählen zu verwenden.
```

### Features, die noch hinzugefügt oder derzeit nicht nutzbar sind:
```bash
  --streaming STREAMING
                        Für Livestreaming (Standard: False).
  --streaming_delay STREAMING_DELAY
                        Die Verzögerung für den Livestream, um Unterbrechungen zu vermeiden. Abhängig von der Geschwindigkeit des Encoders sollte dies angepasst werden (Standard: 30).
  --streamingffmpeg_params STREAMINGFFMPEG_PARAMS
                        Streaming-FFmpeg-Kodierungsparameter (Standard: "-hls_time 6 -hls_flags delete_segments -hls_playlist_type event -hls_segment_type fmp4 -hls_fmp4_init_filename init.mp4 -master_pl_name master.m3u8 output.m3u8").
  --segment_time SEGMENT_TIME
                        Gibt die Dauer jedes Segments in Sekunden an.
  --segment_time_for_client SEGMENT_TIME_FOR_CLIENT
                        Verwenden Sie diese Option, wenn der Benchmark ausgeschaltet ist (Standard: 10).
  --segment_request SEGMENT_REQUEST
                        Verarbeitet Anfragen von Clients für einen Modus, in dem Segmente angefordert werden und der Server sie an die Clients liefert (Standard: False).
  --key KEY             Passwort für die Kodierungsverbindung (Standard: keines).
```

### Client-Optionen:
```bash
options:
  -h, --help            Zeigt diese Hilfe und beendet das Programm.
  --server_ip SERVER_IP
                        IP-Adresse des SocketIO-Servers (Standard: localhost).
  --server_port SERVER_PORT
                        Port des SocketIO-Servers (Standard: 5000).
```
