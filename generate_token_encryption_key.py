#!/usr/bin/env python3
from cryptography.fernet import Fernet
print("TOKEN_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
