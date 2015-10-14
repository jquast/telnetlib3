#!/usr/bin/env python
"""Distutils setup script."""
import os
import setuptools


def _get_install_requires(fname):
    return [req_line.strip() for req_line in open(fname)
            if req_line.strip() and not req_line.startswith('#')]


def _get_version(fname):
    import json
    return json.load(open(fname, 'r'))['version']


setup(name='telnetlib3',
      version='0.2.4',
      url='http://telnetlib3.rtfd.org/',
      license='ISC',
      author='Jeff Quast',
      description="Telnet server and client Protocol library using asyncio",
      long_description=io.open(readme_rst, encoding='utf8').read(),
      packages=['telnetlib3', 'telnetlib3.contrib', ],
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
      tests_require=['pytest'],
      install_requires=install_requires,
      cmdclass={'test': PyTest},
      extras_require={'testing': ['pytest'], },
      test_suite='tests',
      )
