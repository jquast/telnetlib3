#!/usr/bin/env python
import os
import platform
from setuptools import setup


def _get_here(fname):
    return os.path.join(os.path.dirname(__file__), fname)


def _get_long_description(fname, encoding='utf8'):
    return open(fname, 'r', encoding=encoding).read()


def _get_install_requires():
    if platform.python_version_tuple() < ('3', '4'):
        return ['asyncio']
    return []

def _get_version(fname):
    import json
    return json.load(open(fname, 'r'))['version']


setup(name='telnetlib3',
      version=_get_version(fname=_get_here('version.json')),
      url='http://telnetlib3.rtfd.org/',
      license='ISC',
      author='Jeff Quast',
      description="Python 3 asyncio Telnet server and client Protocol library",
      long_description=_get_long_description(fname=_get_here('README.rst')),
      packages=['telnetlib3'],
      package_data={'': ['README.rst', 'requirements.txt'], },
      entry_points={
         'console_scripts': [
             'telnetlib3-server = telnetlib3.server:main',
             'telnetlib3-client = telnetlib3.client:main'
         ]},
      author_email='contact@jeffquast.com',
      platforms='any',
      zip_safe=True,
      keywords=', '.join(('telnet', 'server', 'client', 'bbs', 'mud', 'utf8',
                          'cp437', 'api', 'library', 'asyncio', 'talker')),
      classifiers=['License :: OSI Approved :: ISC License (ISCL)',
                   'Programming Language :: Python :: 3.3',
                   'Programming Language :: Python :: 3.4',
                   'Intended Audience :: Developers',
                   'Development Status :: 4 - Beta',
                   'Topic :: System :: Networking',
                   'Topic :: Terminals :: Telnet',
                   'Topic :: System :: Shells',
                   'Topic :: Internet',
                   ],
      install_requires=_get_install_requires(),
      )
