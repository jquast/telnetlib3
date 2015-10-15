#!/usr/bin/env python
"""Distutils setup script."""
import os
from setuptools import setup


def _get_here(fname):
    return os.path.join(os.path.dirname(__file__), fname)


def _get_long_description(fname, encoding='utf8'):
    return open(fname, 'r', encoding=encoding).read()


def _get_install_requires(fname):
    return [req_line.strip() for req_line in open(fname, 'r')
            if req_line.strip() and not req_line.startswith('#')]


def _get_version(fname):
    import json
    return json.load(open(fname, 'r'))['version']


setup(name='telnetlib3',
      version=_get_version(fname=_get_here('version.json')),
      url='http://telnetlib3.rtfd.org/',
      license='ISC',
      author='Jeff Quast',
      description="Telnet server and client Protocol library using asyncio",
      long_description=_get_long_description(fname=_get_here('README.rst')),
      packages=['telnetlib3', ],
      package_data={'': ['README.rst', 'requirements.txt', ], },
      scripts=['bin/telnet-client',
               'bin/telnet-server',
               'bin/telnet-talker', ],
      author_email='contact@jeffquast.com',
      platforms='any',
      keywords=', '.join(('telnet', 'server', 'client', 'bbs', 'mud', 'utf8',
                          'cp437', 'api', 'library', 'asyncio', 'talker',
                          'tulip', )),
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
      install_requires=_get_install_requires(_get_here('requirements.txt')),
      )
