#!/bin/sh
# Controllo del Cookie di sessione
if ! echo "$HTTP_COOKIE" | grep -q "nvr_session=autorizzato"; then
    echo "Status: 302 Moved Temporarily"
    echo "Location: login.sh"
    echo ""
    exit
fi

echo "Content-type: text/html; charset=utf-8"
echo ""

CAM=$(echo "$QUERY_STRING" | sed -n 's/^.*cam=\([0-9]\).*$/\1/p')
[ -z "$CAM" ] && CAM="1"

if [ "$CAM" = "1" ]; then
    CAM_NAME="Soggiorno"
elif [ "$CAM" = "3" ]; then
    CAM_NAME="Corridoio"
else
    CAM_NAME="Box"
fi

PHYSICAL_DIR="/mnt/sda5/nvr/camera$CAM"
WEB_PATH="../archivio/camera$CAM"

cat <<EOF
<!DOCTYPE html>
<html>
<head>
    <meta charset='UTF-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no'>
    <link rel="manifest" href="../manifest.json">
    <style>
        :root { --bg: #0a0a0a; --card: #141414; --accent: #007bff; --success: #28a745; --danger: #ff3b30; --text: #fff; --border: #222; }
        * { box-sizing: border-box; }
        body { background: var(--bg); color: var(--text); font-family: -apple-system, sans-serif; margin: 0; padding: 0; min-height: 100vh; }
        
        /* NAVBAR SUPERIORE */
        .back-nav { 
            position: sticky; top: 0; background: rgba(15,15,15,0.95); padding: 10px 14px; 
            z-index: 1000; border-bottom: 1px solid var(--border); backdrop-filter: blur(10px); 
            display: flex; justify-content: space-between; align-items: center; 
        }
        .back-nav a { color: var(--accent); text-decoration: none; font-weight: bold; font-size: 13px; }
        .back-nav h2 { margin: 0; font-size: 14px; font-weight: 500; text-transform: uppercase; letter-spacing: 1px; color: #ccc; }

        .container { max-width: 1200px; margin: 0 auto; padding: 10px; }

        /* GRIGLIA ARCHIVIO VIDEO (2 COLONNE SU MOBILE, AUTO-FILL SU DESKTOP) */
        .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
        
        @media (min-width: 768px) {
            .grid { grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
            .container { padding: 16px; }
        }

        .card { background: var(--card); border-radius: 10px; overflow: hidden; border: 1px solid var(--border); display: flex; flex-direction: column; }
        
        /* THUMBNAIL CON ICONA PLAY TRASPARENTE SENZA SFONDO CELESTE */
        .thumb-container { width: 100%; aspect-ratio: 16/9; background: #000; cursor: pointer; position: relative; overflow: hidden; }
        .card img { width: 100%; height: 100%; object-fit: cover; transition: transform 0.3s; }
        .card:hover img { transform: scale(1.03); }
        
        .play-overlay { position: absolute; top:0; left:0; width:100%; height:100%; display:flex; align-items:center; justify-content:center; background: rgba(0,0,0,0.2); transition: background 0.2s; }
        .thumb-container:hover .play-overlay { background: rgba(0,0,0,0.05); }
        
        /* ICONA PLAY SOLTANTO SIMBOLO TRASPARENTE SENZA SFONDO */
        .play-icon-btn { 
            background: transparent; border: none; padding: 0; margin: 0;
            color: rgba(255, 255, 255, 0.85); font-size: 38px; line-height: 1;
            filter: drop-shadow(0 2px 8px rgba(0,0,0,0.7)); 
            transition: transform 0.2s, color 0.2s, opacity 0.2s; 
        }
        
        @media (min-width: 768px) {
            .play-icon-btn { font-size: 48px; }
        }
        
        .thumb-container:hover .play-icon-btn { transform: scale(1.18); color: rgba(255, 255, 255, 1); }

        .info-bar { padding: 8px 6px; font-size: 10px; color: #aaa; text-align: center; background: #111; border-top: 1px solid #1a1a1a; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        
        /* TASTO SCARICA COMPATTO */
        .btn-dl { 
            display: block; margin: 0 6px 6px 6px; padding: 8px 0; 
            background: #007bff; color: #fff; text-decoration: none; 
            border-radius: 6px; font-size: 11px; font-weight: bold; 
            text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.2); transition: 0.15s;
        }
        @media (min-width: 768px) {
            .btn-dl { margin: 0 8px 8px 8px; padding: 10px 0; font-size: 12px; border-radius: 8px; }
        }
        .btn-dl:active { background: #0056b3; transform: scale(0.98); }

        /* MODALE PLAYER VIDEO FULLSCREEN CON CONTROLLI DI NAVIGAZIONE E SEEKING */
        #videoPlayerContainer { 
            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; 
            background: rgba(0,0,0,0.95); z-index: 2500; flex-direction: column; align-items: center; justify-content: center; 
            backdrop-filter: blur(12px); padding: 12px;
        }
        .player-content { width: 100%; max-width: 960px; background: #111; border-radius: 12px; border: 1px solid #333; overflow: hidden; position: relative; box-shadow: 0 10px 40px rgba(0,0,0,0.8); }
        .player-video-wrapper { width: 100%; aspect-ratio: 16/9; background: #000; position: relative; }
        .player-video-wrapper video { width: 100%; height: 100%; max-height: 75vh; }

        .player-bar { display: flex; justify-content: space-between; align-items: center; padding: 12px 14px; background: #161616; border-top: 1px solid #222; flex-wrap: wrap; gap: 8px; }
        .player-title { font-size: 11px; color: #ddd; font-weight: bold; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 45%; }
        
        .player-seek-group { display: flex; gap: 6px; align-items: center; }
        .btn-seek-bar { background: #222; border: 1px solid #333; color: #fff; padding: 5px 10px; border-radius: 6px; font-size: 11px; font-weight: bold; cursor: pointer; }
        .btn-seek-bar:active { background: var(--accent); }

        .player-modal-actions { display: flex; gap: 8px; align-items: center; }
        .btn-modal-dl { background: var(--success); color: #fff; padding: 6px 12px; border-radius: 6px; font-size: 11px; font-weight: bold; text-decoration: none; }
        .close-player-btn { background: #333; color: #fff; border: none; padding: 6px 10px; border-radius: 6px; font-size: 13px; cursor: pointer; font-weight: bold; }
        .close-player-btn:hover { background: var(--danger); }
    </style>
</head>
<body>

<!-- MODALE RIPRODUTTORE VIDEO CON NAVIGAZIONE E SEEKING -->
<div id='videoPlayerContainer' onclick='checkCloseModal(event)'>
    <div class='player-content' onclick='event.stopPropagation()'>
        <div class='player-video-wrapper'>
            <video id='mainVideo' controls playsinline preload='auto' autoplay></video>
        </div>
        <div class='player-bar'>
            <div id='videoTitle' class='player-title'>Registrazione MP4</div>
            
            <!-- TASTI NAVIGAZIONE AVANTI / INDIETRO -->
            <div class='player-seek-group'>
                <button class='btn-seek-bar' onclick='seekVideo(-10)'>&#9194; -10s</button>
                <button class='btn-seek-bar' onclick='seekVideo(10)'>&#9193; +10s</button>
            </div>

            <div class='player-modal-actions'>
                <a id='modalDownloadBtn' href='#' download class='btn-modal-dl'>&#128190; SCARICA</a>
                <button class='close-player-btn' onclick='closeVideo()'>&#10005; CHIUDI</button>
            </div>
        </div>
    </div>
</div>

<!-- TOP NAV -->
<div class='back-nav'>
    <a href='/cgi-bin/home.sh'>&larr; LIVE CAMERE</a>
    <h2>Archivio $CAM_NAME</h2>
    <div style="width: 70px;"></div>
</div>

<div class='container'>
    <div class='grid'>
EOF

FILES=$(ls -1r "$PHYSICAL_DIR"/*.mp4 2>/dev/null)

if [ -z "$FILES" ]; then
    echo "<div style='grid-column: 1/-1; padding: 40px; text-align: center; color: #666; font-size: 13px;'>Nessuna registrazione video trovata in archivio.</div>"
fi

for full_path in $FILES; do
    [ -f "$full_path" ] || continue
    f=$(basename "$full_path")
    thumb="${f%.mp4}.jpg"
    rel_file="camera$CAM/$f"
    
    echo "<div class='card'>"
    echo "<div class='thumb-container' onclick=\"playVideo('$rel_file', '$WEB_PATH/$f', '$f')\">"
    echo "<img src='$WEB_PATH/$thumb' onerror=\"this.style.opacity='0';\">"
    echo "<div class='play-overlay'><div class='play-icon-btn'>&#9654;</div></div>"
    echo "</div>"
    echo "<div class='info-bar'>$f</div>"
    echo "<a href='$WEB_PATH/$f' download class='btn-dl'>&#128190; SCARICA</a>"
    echo "</div>"
done

cat <<EOF
    </div>
</div>

<script>
    // Modal Player Video con streaming HTTP Range (HTTP 206) per seeking istantaneo
    function playVideo(relFile, dlUrl, title) {
        const container = document.getElementById('videoPlayerContainer');
        const video = document.getElementById('mainVideo');
        const titleEl = document.getElementById('videoTitle');
        const dlBtn = document.getElementById('modalDownloadBtn');
        
        // Stream tramite CGI con supporto HTTP Range per saltare avanti/indietro sulla timeline
        const streamUrl = 'stream.py?file=' + encodeURIComponent(relFile);
        
        video.src = streamUrl;
        video.preload = "auto";
        titleEl.innerText = title;
        dlBtn.href = dlUrl;
        
        container.style.display = 'flex';
        video.load();
        video.play().catch(err => console.log('Autoplay bloccato o errore riproduzione'));
    }

    function closeVideo() {
        const container = document.getElementById('videoPlayerContainer');
        const video = document.getElementById('mainVideo');
        video.pause();
        video.src = '';
        container.style.display = 'none';
    }

    // Funzione per saltare avanti e indietro nei secondi
    function seekVideo(seconds) {
        const video = document.getElementById('mainVideo');
        if (video && !isNaN(video.duration)) {
            video.currentTime = Math.min(Math.max(video.currentTime + seconds, 0), video.duration);
        }
    }

    function checkCloseModal(e) {
        if (e.target.id === 'videoPlayerContainer') {
            closeVideo();
        }
    }

    // Navigazione da tastiera: Freccia Sinistra (-5s), Freccia Destra (+5s), Spazio (Play/Pausa), ESC (Chiudi)
    document.addEventListener('keydown', function(e) {
        const container = document.getElementById('videoPlayerContainer');
        if (container.style.display === 'flex') {
            const video = document.getElementById('mainVideo');
            if (e.key === 'ArrowLeft') {
                e.preventDefault();
                seekVideo(-5);
            } else if (e.key === 'ArrowRight') {
                e.preventDefault();
                seekVideo(5);
            } else if (e.key === ' ') {
                e.preventDefault();
                if (video.paused) video.play(); else video.pause();
            } else if (e.key === 'Escape') {
                closeVideo();
            }
        }
    });

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js')
        .then(reg => console.log('Service Worker registrato!', reg))
        .catch(err => console.error('Errore registrazione SW:', err));
    });
  }
</script>
</body>
</html>
EOF
