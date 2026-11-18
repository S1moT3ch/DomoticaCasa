# Script per il Trampolino UEFI verso Linux (Eseguito all'avvio come SYSTEM)
$openwrtBootUrl = "http://192.168.1.162:5050/get_next_boot"
$ubuntuGuid = "{1ac58902-ddea-11f1-9c19-806e6f6e6963}"

# Attendi che la rete sia attiva e risponda al servizio (fino a 45 secondi)
$maxWait = 45
$started = [DateTime]::UtcNow

while (([DateTime]::UtcNow - $started).TotalSeconds -lt $maxWait) {
    try {
        $resp = Invoke-RestMethod -Uri $openwrtBootUrl -TimeoutSec 3 -UseBasicParsing
        if ($resp -and $resp.target_os -eq "linux") {
            # Imposta la sequenza di boot UEFI per il prossimo avvio verso Ubuntu
            & bcdedit /set "{fwbootmgr}" bootsequence "$ubuntuGuid"
            # Riavvia immediatamente
            & shutdown /r /f /t 0
            exit 0
        } elseif ($resp -and $resp.target_os -eq "windows") {
            # Boot confermato su Windows
            exit 0
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}

