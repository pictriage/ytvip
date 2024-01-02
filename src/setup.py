from setuptools import setup, find_packages
from pathlib import Path
import shutil

requirements_txt = Path(__file__).parent.joinpath('requirements.txt').read_text('utf8')
install_requires = []
for line in requirements_txt.splitlines(keepends=False):
    line = line.strip()
    if line:
        install_requires.append(line)

CMD_NAME = 'ytvip'

if Path('dist').is_dir():
    shutil.rmtree('dist')

# to deploy:
# python setup.py sdist
# twine upload dist/*

# note that bdist_wheel doesn't upload stuff in MANIFEST.in

setup(
    name=CMD_NAME,
    version='0.0.9',
    packages=find_packages(),
    include_package_data=True,
    url='',
    license='',
    author='User',
    author_email='',
    description='',
    install_requires=install_requires,
    entry_points={
        'console_scripts': [
            f'{CMD_NAME}=ytcl:main',
        ],
    },
)

if Path('build').is_dir():
    shutil.rmtree('build')
