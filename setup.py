from setuptools import setup, find_packages

setup(
    name="TRADE",
    version="0.2.0",
    packages=find_packages(),
    install_requires=[
        "numpy",
        "pandas",
        "scipy",
        "websocket-client",
        "requests",
    ],
    entry_points={
        'console_scripts': [
            'trade=TRADE.main:main',
        ],
    },
)