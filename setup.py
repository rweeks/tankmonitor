from setuptools import setup

"""
The setup.py file is a convention used by Python programmers. The command
`python setup.py install` will install the required dependencies that are
not affiliated with the Raspberry Pi.
"""

setup(
    name="tankmonitor",
    url='https://github.com/rweeks/tankmonitor',
    packages=['tankmonitor'],
    install_requires=[
        'sockjs-tornado',
        'pillow',
        'netifaces',
        "pyserial"
    ]
)

