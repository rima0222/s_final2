from cryptography.fernet import Fernet
from flask import current_app

def _fernet():
    return Fernet(current_app.config["DATA_KEY"].encode())

def encrypt(value):
    return _fernet().encrypt(value.encode()).decode()

def decrypt(value):
    return _fernet().decrypt(value.encode()).decode()
