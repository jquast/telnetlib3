#! /usr/bin/env python
"""
Distribution file for telnetlib3
"""
from distutils.core import setup
import os

setup(name='telnetlib3',
      version='0.1',
      description="Telnet Protocol server and shell using tulip / PEP3156.",
      long_description=open(
          os.path.join(os.path.dirname(__file__), 'README')).read(),
      author='Jeff Quast',
      author_email='contact@jeffquast.com',
      url='http://telnetlib3.rtfd.org/',
      keywords='telnet, server, bbs, mud, utf8, honeypot',
      license='ISC',
      packages=['telnetlib3',],
      classifiers=[
          'License :: OSI Approved :: ISC License (ISCL)',
          'Programming Language :: Python :: 3.4 :: Only',
          'Intended Audience :: Developers',
          'Development Status :: 4 - Beta',
          'Topic :: System :: Networking',
          'Topic :: Terminals :: Telnet',
          'Topic :: System :: Shells',
          'Topic :: Internet',
      ], )
