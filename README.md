### English Version:

# FFmpeg-Cluster
FFmpeg Cluster is a distributed system designed for efficient video processing. It allows multiple clients to collaborate in encoding video segments, which are then combined into a single output file. This project uses Flask-SocketIO for communication between the server and clients and FFmpeg for video processing.

## Usage:

Start the server and specify the number of clients with `--required_clients` that should work on your video. The video file must be in the same directory as the server. You can pass encoding parameters with `--ffmpeg_params` as shown in the example, and set the output filename with `--file_name_output`.

### Requirements:
- [FFmpeg](https://github.com/btbn/ffmpeg-builds/releases)
- requests
- Flask
- Flask-SocketIO
- eventlet
- numpy
- [requirements.txt](https://raw.githubusercontent.com/JanisPlayer/FFmpeg-Cluster/refs/heads/main/requirements.txt)

### Setup:
```bash
git clone https://github.com/JanisPlayer/FFmpeg-Cluster.git
cd FFmpeg-Cluster
sudo apt install python3 python3-pip python3.12-venv ffmpeg -y
python3 -m venv ./FFmpeg-Cluster-venv
source FFmpeg-Cluster-venv/bin/activate
pip install -r requirements.txt
```

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

Why Clients Finish at Different Speeds Despite Segmentation: During segmentation, it is not determined how demanding a particular segment is, meaning whether it contains a lot of changing information or just a still image. Estimating how resource-intensive a segment is would take too much time and processing power, as it would require encoding the video at various points within the segment at regular intervals to approximate the processing requirements. Since each encoder operates differently, it would also not be straightforward to simply observe where the most changes occur in the image. Thus, this is a problem that is likely to persist.

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

### TODO and Ideas:

- **Make audio parameters applicable:**
  Audio parameters should be applied on the server by converting the audio from the original file. Alternatively, audio encoding could be handled separately on the clients. However, since audio encoding is fast on a PC, this might not be urgent or even necessary.

- **FFmpeg parameter overwrite (ffmpeg_params_overwrite):**
  Allows the client to use hardware encoders.

- **Parameter for the key and associated system:**
  Introduce a system for clients and servers based on a parameter to enhance security.

- **Parameter to override benchmark frames:**
  A parameter to allow overriding the number of benchmark frames for the client.

- **File access:**
  Allow the client to also use files from other folders.

- **Installer and Docker support:**
  Provide an installer and a Docker environment.

- **Experimental `segment_request` function:**
  This function aims to improve speed through better parallelization at the cost of efficiency by dividing the video into more segments. If a client finishes its segment earlier than expected, it will be assigned a new segment. The idea is to continuously assign clients new parts of the video that haven't been encoded yet. This improves load distribution and could also be useful for streaming.

  **Technical Explanation:**
  After benchmarking (or without benchmarking), each client is assigned a part of the video. Once a client has encoded its segment, a global variable protected by thread locks is used to determine which frame the video was last processed. The number of frames remaining until the end of the video is checked:

  - If fewer than 10 seconds (in frames) remain, the current client receives all remaining frames.
  - Otherwise, it receives only its assigned or benchmark-determined portion.

  This approach generates more unnecessary B-frames but allows better load balancing, even when encoding effort varies between segments. This is currently just an idea that could be implemented and tested soon.

- **Divide the video into 10-second segments:**
  Benchmarking determines how many frames of a segment each client takes.

```bash
python3 server.py --required_clients 2 --file_name input.mp4 --segment_request --segment_time 10 --ffmpeg_params "-c:v libsvtav1 -preset 6 -crf 30"
```

- **Minimal segment time of 10 seconds with benchmarking:**
  Using negative values for `segment_time` ensures the weakest client is used as the baseline.

```bash
python3 server.py --required_clients 2 --file_name input.mp4 --segment_request --segment_time -10 --ffmpeg_params "-c:v libsvtav1 -preset 6 -crf 30"
```

- **10-second segments without benchmarking:**

```bash
python3 server.py --required_clients 2 --file_name input.mp4 --segment_request --segment_time 10 --benchmark_seconds 0 --ffmpeg_params "-c:v libsvtav1 -preset 6 -crf 30"
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
- requests

### Installation:
```bash
git clone https://github.com/JanisPlayer/FFmpeg-Cluster.git
cd FFmpeg-Cluster
sudo apt install python3 python3-pip python3.12-venv ffmpeg -y
python3 -m venv ./FFmpeg-Cluster-venv
source FFmpeg-Cluster-venv/bin/activate
pip install -r requirements.txt
```

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

Warum die Clients trotz Aufteilung unterschiedlich schnell fertig werden: Bei der Aufteilung wird nicht ermittelt, wie anspruchsvoll ein bestimmtes Segment ist, also ob es viele sich verändernde Informationen enthält oder nur ein Standbild. Da eine Schätzung, wie aufwändig ein Segment ist, zu viel Zeit und Leistung kosten würde, müsste man für diese Schätzung an verschiedenen Punkten in regelmäßigen Abständen im Segment das Video für eine gewisse Zeit encodieren, um herauszufinden, wie groß die benötigte Leistung für das Segment ungefähr ist. Da jeder Encoder unterschiedlich arbeitet, wäre es auch nicht einfach, lediglich die Stellen zu betrachten, an denen die meisten Veränderungen im Bild stattfinden. Das ist also ein Problem, das wahrscheinlich bestehen bleibt.

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

### TODO und Ideen:

- **Audio-Parameter anwendbar machen:**
  Audio-Parameter sollen beim Server angewendet werden, indem die Audiodatei von der Originaldatei konvertiert wird. Alternativ könnte die Audioencodierung auch separat auf den Clients erfolgen. Da das Encodieren von Audio auf einem PC jedoch schnell geht, ist dies weniger dringend und möglicherweise unnötig.

- **FFmpeg-Parameter-Überschreibung (ffmpeg_params_overwrite):**
  Ermöglicht es, dass der Client Hardware-Encoder nutzen kann.

- **Parameter für den Key und das zugehörige System:**
  Einführung eines Systems für Client und Server, das auf einem Parameter basiert, um die Sicherheit zu verbessern.

- **Parameter für Benchmark-Frames:**
  Ein Parameter, der es erlaubt, die Anzahl der Benchmark-Frames des Clients zu überschreiben.

- **Dateizugriff:**
  Dem Client ermöglichen, auch Dateien aus anderen Ordnern zu nutzen.

- **Installer und Docker-Support:**
  Bereitstellung eines Installers und einer Docker-Umgebung.

- **Experimentelle `segment_request`-Funktion:**
  Diese Funktion soll die Geschwindigkeit durch bessere Parallelisierung auf Kosten der Effizienz beschleunigen. Dabei wird das Video in mehr Segmente unterteilt, sodass ein Client, der früher als geplant mit seinem Segment fertig wird, ein neues Segment zugewiesen bekommt. Die Idee besteht darin, Clients kontinuierlich neue Abschnitte des Videos zuzuweisen, die noch nicht encodiert wurden. Dies verbessert die Lastverteilung und könnte auch für Streaming nützlich sein.

  **Technische Erläuterung:**
  Nach dem Benchmark (oder ohne Benchmark) wird jedem Client ein Teil des Videos gesendet. Sobald ein Client sein Segment encodiert hat, wird aus einer globalen Variable, die mit Thread-Locks geschützt ist, abgerufen, bis zu welchem Frame das Video zuletzt verarbeitet wurde. Es wird geprüft, wie viele Frames bis zum Ende des Videos übrig sind:

  - Sind es weniger als 10 Sekunden (in Frames), erhält der aktuelle Client alle verbleibenden Frames.
  - Andernfalls erhält er nur den ihm zugewiesenen oder durch den Benchmark ermittelten Anteil.

  Diese Methode erzeugt zwar mehr unnötige B-Frames, erlaubt es aber, die Last bei unterschiedlichem Encodierungsaufwand pro Segment besser zu verteilen. Dies ist aktuell nur eine Idee, die in naher Zukunft umgesetzt und getestet werden könnte.

- **Aufteilung in 10-Sekunden-Segmente:**
  Das Benchmark entscheidet, welcher Client wie viele Frames eines Segments übernimmt.

```bash
python3 server.py --required_clients 2 --file_name input.mp4 --segment_request --segment_time 10 --ffmpeg_params "-c:v libsvtav1 -preset 6 -crf 30"
```

- **Minimale Segmentzeit von 10 Sekunden mit Benchmark:**
  Bei Verwendung negativer Werte für `segment_time` sorgt das Benchmark dafür, dass der schwächste Client als Basis genutzt wird.

```bash
python3 server.py --required_clients 2 --file_name input.mp4 --segment_request --segment_time -10 --ffmpeg_params "-c:v libsvtav1 -preset 6 -crf 30"
```

- **10-Sekunden-Segmente ohne Benchmark:**

```bash
python3 server.py --required_clients 2 --file_name input.mp4 --segment_request --segment_time 10 --benchmark_seconds 0 --ffmpeg_params "-c:v libsvtav1 -preset 6 -crf 30"
```
