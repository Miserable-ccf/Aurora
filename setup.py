"""Compatibility metadata for environments with setuptools older than PEP 621 support."""

from setuptools import find_packages, setup


setup(
    name="aurora-monitor",
    version="0.1.0",
    description="Lightweight and auditable Jiangsu recruitment notice monitor",
    packages=find_packages(include=["aurora_monitor", "aurora_monitor.*", "aurora_web", "aurora_web.*"]),
    python_requires=">=3.10",
    extras_require={
        "config": ["PyYAML>=6.0"],
        "xlsx": ["openpyxl>=3.1"],
        "pdf": ["PyMuPDF>=1.24"],
        "documents": ["openpyxl>=3.1", "PyMuPDF>=1.24"],
        "all": ["PyYAML>=6.0", "openpyxl>=3.1", "PyMuPDF>=1.24"],
        "web": ["fastapi>=0.110,<1", "uvicorn>=0.29,<1"],
    },
    entry_points={"console_scripts": [
        "aurora-monitor=aurora_monitor.__main__:main",
        "aurora-web=aurora_web.__main__:main",
    ]},
    package_data={"aurora_web": ["static/*.html", "static/*.css", "static/*.js"]},
)
