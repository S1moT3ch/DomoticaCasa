try:
    from sinric import SinricPro
    print("Metodi di SinricPro disponibili:")
    print([m for m in dir(SinricPro) if not m.startswith('_')])
except Exception as e:
    print("Errore:", e)
