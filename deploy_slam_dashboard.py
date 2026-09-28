"""Run on Pi after uploading the locally prepared payload. Preserve live UI."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

root=Path('/home/pi/Drone-model')
stage=Path(tempfile.mkdtemp(prefix='.slam-stage-',dir=root))
with tarfile.open(root/'argus-slam-deploy.tar.gz') as archive:
    for member in archive.getmembers():
        target=(stage/member.name).resolve()
        if not target.is_relative_to(stage) or member.issym() or member.islnk():
            raise RuntimeError('Unexpected payload path')
    archive.extractall(stage)
metadata=json.loads((stage/'patch.json').read_text())
index=root/'argus_dist/index.html'
html=index.read_text()
scripts=re.findall(r'src="(?:\./|/)?assets/([^"/]+\.js)"',html)
if len(scripts)!=1: raise RuntimeError('Unexpected dashboard entry scripts')
original=root/'argus_dist/assets'/scripts[0]
if hashlib.sha256(original.read_bytes()).hexdigest()!=metadata['original_sha256']:
    raise RuntimeError('Pi dashboard changed since inspection; refusing overwrite')
app=root/'app.py'
source=app.read_text()
anchor='ARGUS_DIST_DIR = Path(__file__).parent / "argus_dist"'
if source.count(anchor)!=1 or 'register_slam_routes' in source:
    raise RuntimeError('Unexpected backend integration state')
patched=source.replace(anchor,'from pi_routes import register_slam_routes\nregister_slam_routes(app, worker)\n\n'+anchor)
compile(patched,str(app),'exec')
compile((stage/'pi_routes.py').read_text(),str(root/'pi_routes.py'),'exec')
backup=root/'backups'/('slam-dashboard-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True)
shutil.copy2(app,backup/'app.py')
shutil.copy2(index,backup/'index.html')
shutil.copy2(original,backup/original.name)
if (root/'pi_routes.py').exists(): shutil.copy2(root/'pi_routes.py',backup/'pi_routes.py')
shutil.copytree(stage/'slam_dist',root/'slam_dist',dirs_exist_ok=True)
shutil.copy2(stage/'pi_routes.py',root/'pi_routes.py')
shutil.copy2(stage/metadata['filename'],root/'argus_dist/assets'/metadata['filename'])
shutil.copy2(stage/metadata['stylesheet'],root/'argus_dist/assets'/metadata['stylesheet'])
temporary=app.with_name('app.py.slam-new')
temporary.write_text(patched); temporary.chmod(app.stat().st_mode); temporary.replace(app)
html=html.replace('assets/'+scripts[0],'assets/'+metadata['filename'])
html=html.replace('</head>','<link rel="stylesheet" href="/assets/'+metadata['stylesheet']+'"></head>')
temporary=index.with_suffix('.new'); temporary.write_text(html); temporary.replace(index)
print(json.dumps({'deployed':True,'backup':str(backup),'component':metadata['component']}))
