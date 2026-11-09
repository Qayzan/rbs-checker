"""Make a private migration archive locally; never print its contents."""
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
if not (root / 'bot' / '.env').is_file():
    raise SystemExit('Bot token configuration is missing.')
with tarfile.open(root / 'deploy' / 'rbs-state.tar.gz', 'w:gz') as archive:
    archive.add(root / 'bot' / '.env', arcname='bot.env')
    for name in ('cookies.json', 'buildings.json', 'preferences.json', 'alerts.json'):
        path = root / 'bot' / name
        if path.is_file():
            archive.add(path, arcname=name)
print('Private migration archive prepared. Contents were not printed.')
