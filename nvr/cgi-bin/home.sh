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

# Calcola lo spazio utilizzato sulla partizione /mnt/sda5
STORAGE_INFO=$(df -h /mnt/sda5 | tail -n 1)
TOTAL_SIZE=$(echo "$STORAGE_INFO" | awk '{print $2}')
USED_SIZE=$(echo "$STORAGE_INFO" | awk '{print $3}')
USED_PCT=$(echo "$STORAGE_INFO" | awk '{print $5}' | tr -d '%')

# Calcola lo spazio occupato da ciascuna camera
SIZE_CAM1=$(du -sh /mnt/sda5/nvr/camera1 2>/dev/null | awk '{print $1}')
[ -z "$SIZE_CAM1" ] && SIZE_CAM1="0B"

SIZE_CAM3=$(du -sh /mnt/sda5/nvr/camera3 2>/dev/null | awk '{print $1}')
[ -z "$SIZE_CAM3" ] && SIZE_CAM3="0B"

SIZE_CAM2=$(du -sh /mnt/sda5/nvr/camera2 2>/dev/null | awk '{print $1}')
[ -z "$SIZE_CAM2" ] && SIZE_CAM2="0B"

cat <<EOF
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <link rel="manifest" href="../manifest.json">
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>
        :root { --bg: #0a0a0a; --card: #141414; --accent: #007bff; --danger: #ff3b30; --text: #fff; --border: #222; }
        * { box-sizing: border-box; }
        body { background: var(--bg); color: var(--text); font-family: -apple-system, sans-serif; margin: 0; padding: 0; min-height: 100vh; overflow-x: hidden; }
        
        /* HEADER CON LOGOUT */
        header { 
            display: flex; justify-content: space-between; align-items: center; 
            padding: 8px 16px; background: #000; border-bottom: 1px solid var(--border);
            position: sticky; top: 0; z-index: 1000; height: 42px;
        }
        header h1 { font-size: 13px; font-weight: 400; margin: 0; color: #aaa; letter-spacing: 2px; text-transform: uppercase; }
        .btn-logout { background: #222; color: var(--danger); text-decoration: none; font-size: 10px; font-weight: bold; padding: 4px 10px; border-radius: 5px; border: 1px solid #333; transition: 0.2s; }
        .btn-logout:active { background: #333; }

        /* GRIGLIA TELECAMERE (3 COLONNE PER FIT IN 1 SCREEN SU PC) */
        .container { padding: 10px 14px; max-width: 100%; margin: 0 auto; }
        .cam-grid { display: grid; grid-template-columns: 1fr; gap: 10px; }
        
        @media (min-width: 900px) {
            .cam-grid { grid-template-columns: repeat(3, 1fr); gap: 10px; }
        }

        .cam-card { background: var(--card); border-radius: 10px; overflow: hidden; border: 1px solid var(--border); display: flex; flex-direction: column; }
        .video-box { position: relative; width: 100%; aspect-ratio: 16/9; background: #000; }
        video { width: 100%; height: 100%; object-fit: cover; cursor: pointer; }
        
        .cam-label { position: absolute; top: 8px; left: 8px; background: rgba(0,0,0,0.65); padding: 3px 8px; border-radius: 5px; font-size: 9px; font-weight: bold; backdrop-filter: blur(4px); letter-spacing: 0.5px; }

        /* TOOLBAR CAM */
        .cam-toolbar { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; border-top: 1px solid var(--border); background: var(--card); }
        .cam-name { font-size: 13px; font-weight: 500; color: #fff; }
        .btn-toggle-ptz { background: #222; border: 1px solid #333; color: var(--accent); padding: 6px 10px; border-radius: 6px; font-size: 10px; cursor: pointer; font-weight: bold; }

        /* PTZ DRAWER */
        .ptz-drawer { 
            display: none; padding: 12px 0; background: #0f0f0f; border-top: 1px solid #222;
            animation: slideDown 0.2s ease-in-out; 
        }
        @keyframes slideDown { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }

        .ptz-grid { display: grid; grid-template-columns: repeat(3, 48px); gap: 8px; justify-content: center; }
        .btn-ptz { width: 48px; height: 48px; background: #1a1a1a; border: 1px solid #333; color: white; border-radius: 50%; font-size: 16px; cursor: pointer; transition: 0.1s; }
        .btn-ptz:active { background: var(--accent); transform: scale(0.92); }

        /* FOOTER / ARCHIVIO E MEMORIA (OTTIMIZZATO IN ALTEZZA) */
        .footer-area { margin-top: 10px; padding: 12px 14px; background: #000; border-top: 1px solid var(--border); text-align: center; }
        .btn-archive-group { display: flex; gap: 10px; justify-content: center; margin-bottom: 12px; flex-wrap: wrap; }
        .btn-archive { flex: 1; min-width: 120px; max-width: 220px; padding: 10px 12px; background: #28a745; color: white; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 11px; text-align: center; transition: 0.2s; box-shadow: 0 2px 4px rgba(0,0,0,0.3); }
        .btn-archive:active { transform: scale(0.98); opacity: 0.9; }

        .storage-box { max-width: 500px; margin: 0 auto; background: #0a0a0a; border: 1px solid #222; border-radius: 8px; padding: 10px 14px; }
        .storage-info { display: flex; justify-content: space-between; font-size: 9px; color: #888; margin-bottom: 6px; letter-spacing: 0.5px; font-weight: 600; }
        .progress-bar { background: #1a1a1a; height: 5px; border-radius: 3px; overflow: hidden; margin-bottom: 10px; }
        .progress-fill { background: var(--accent); height: 100%; width: $USED_PCT%; transition: width 1s ease-in-out; }
        
        .cam-storage-grid { display: flex; justify-content: space-between; gap: 8px; }
        .cam-storage-item { flex: 1; background: #141414; border: 1px solid #222; border-radius: 6px; padding: 6px 4px; display: flex; flex-direction: column; align-items: center; }
        .cam-storage-name { font-size: 9px; color: #888; font-weight: 600; margin-bottom: 2px; }
        .cam-storage-val { font-size: 11px; color: var(--accent); font-weight: bold; }
    </style>
</head>
<body>

<header>
    <div style="width: 50px;"></div> <h1>NVR Dashboard</h1>
    <a href="logout.sh" class="btn-logout">ESCI</a>
</header>

<div class="container">
    <div class="cam-grid">
        
        <!-- CAMERA 1 (SOGGIORNO) -->
        <div class="cam-card">
            <div class="video-box">
                <div class="cam-label">LIVE - SOGGIORNO</div>
                <video id="video1" muted playsinline></video>
            </div>
            <div class="cam-toolbar">
                <div class="cam-name">Camera 1</div>
                <button class="btn-toggle-ptz" onclick="togglePTZ('ptz1')">CONTROLLI PTZ</button>
            </div>
            <div id="ptz1" class="ptz-drawer">
                <div class="ptz-grid">
                    <div></div><button class="btn-ptz" onpointerdown="ptzExec('1','up')">&#9650;</button><div></div>
                    <button class="btn-ptz" onpointerdown="ptzExec('1','left')">&#9664;</button>
                    <button class="btn-ptz" onpointerdown="ptzExec('1','stop')" style="background:#333;">&#9632;</button>
                    <button class="btn-ptz" onpointerdown="ptzExec('1','right')">&#9654;</button>
                    <div></div><button class="btn-ptz" onpointerdown="ptzExec('1','down')">&#9660;</button><div></div>
                </div>
            </div>
        </div>

        <!-- CAMERA 3 (CORRIDOIO) -->
        <div class="cam-card">
            <div class="video-box">
                <div class="cam-label">LIVE - CORRIDOIO</div>
                <video id="video3" muted playsinline></video>
            </div>
            <div class="cam-toolbar">
                <div class="cam-name">Camera 3</div>
                <button class="btn-toggle-ptz" onclick="togglePTZ('ptz3')">CONTROLLI PTZ</button>
            </div>
            <div id="ptz3" class="ptz-drawer">
                <div class="ptz-grid">
                    <div></div><button class="btn-ptz" onpointerdown="ptzExec('3','up')">&#9650;</button><div></div>
                    <button class="btn-ptz" onpointerdown="ptzExec('3','left')">&#9664;</button>
                    <button class="btn-ptz" onpointerdown="ptzExec('3','stop')" style="background:#333;">&#9632;</button>
                    <button class="btn-ptz" onpointerdown="ptzExec('3','right')">&#9654;</button>
                    <div></div><button class="btn-ptz" onpointerdown="ptzExec('3','down')">&#9660;</button><div></div>
                </div>
            </div>
        </div>

        <!-- CAMERA 2 (BOX) -->
        <div class="cam-card">
            <div class="video-box">
                <div class="cam-label">LIVE - BOX</div>
                <video id="video2" muted playsinline></video>
            </div>
            <div class="cam-toolbar">
                <div class="cam-name">Camera 2</div>
                <button class="btn-toggle-ptz" onclick="togglePTZ('ptz2')">CONTROLLI PTZ</button>
            </div>
            <div id="ptz2" class="ptz-drawer">
                <div class="ptz-grid">
                    <div></div><button class="btn-ptz" onpointerdown="ptzExec('2','up')">&#9650;</button><div></div>
                    <button class="btn-ptz" onpointerdown="ptzExec('2','left')">&#9664;</button>
                    <button class="btn-ptz" onpointerdown="ptzExec('2','stop')" style="background:#333;">&#9632;</button>
                    <button class="btn-ptz" onpointerdown="ptzExec('2','right')">&#9654;</button>
                    <div></div><button class="btn-ptz" onpointerdown="ptzExec('2','down')">&#9660;</button><div></div>
                </div>
            </div>
        </div>

    </div>
</div>

<div class="footer-area">
    <div class="btn-archive-group">
        <a href="archivio.sh?cam=1" class="btn-archive">&#128194; ARCHIVIO Soggiorno</a>
        <a href="archivio.sh?cam=3" class="btn-archive">&#128194; ARCHIVIO Corridoio</a>
        <a href="archivio.sh?cam=2" class="btn-archive">&#128194; ARCHIVIO Box</a>
    </div>
    <div class="storage-box">
        <div class="storage-info"><span>SPAZIO USB TOTALE ($USED_PCT%)</span><span>$USED_SIZE / $TOTAL_SIZE</span></div>
        <div class="progress-bar"><div class="progress-fill"></div></div>
        
        <div class="cam-storage-grid">
            <div class="cam-storage-item">
                <span class="cam-storage-name">🛋️ Soggiorno</span>
                <span class="cam-storage-val">$SIZE_CAM1</span>
            </div>
            <div class="cam-storage-item">
                <span class="cam-storage-name">🚪 Corridoio</span>
                <span class="cam-storage-val">$SIZE_CAM3</span>
            </div>
            <div class="cam-storage-item">
                <span class="cam-storage-name">🛡️ Box</span>
                <span class="cam-storage-val">$SIZE_CAM2</span>
            </div>
        </div>
    </div>
</div>

<script>
    function startHls(id, source) {
        var video = document.getElementById(id);
        if (Hls.isSupported()) {
            var hls = new Hls({ enableWorker: true, lowLatencyMode: true });
            hls.loadSource(source); hls.attachMedia(video);
            hls.on(Hls.Events.MANIFEST_PARSED, () => video.play());
        }
    }

    startHls('video1', '../cam1_live/live.m3u8');
    startHls('video3', '../cam3_live/live.m3u8');
    startHls('video2', '../cam2_live/live.m3u8');

    function togglePTZ(id) {
        var drawer = document.getElementById(id);
        var isVisible = drawer.style.display === 'block';
        document.querySelectorAll('.ptz-drawer').forEach(d => d.style.display = 'none');
        drawer.style.display = isVisible ? 'none' : 'block';
    }

    function ptzExec(cam, move) {
        if (window.navigator.vibrate) window.navigator.vibrate(50);
        fetch('ptz.sh?cam=' + cam + '&move=' + move + '&t=' + Date.now());
    }

    // Fullscreen nativo
    document.querySelectorAll('video').forEach(v => {
        v.addEventListener('dblclick', function() {
            if (this.requestFullscreen) this.requestFullscreen();
            else if (this.webkitRequestFullscreen) this.webkitRequestFullscreen();
        });
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
