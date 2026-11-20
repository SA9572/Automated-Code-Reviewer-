SECURITY_RULES = {
    "SEC001": {
        "name": "SQL Injection",
        "description": "Unsafe SQL query construction using untrusted input.",
        "severity": "CRITICAL",
    },
    "SEC002": {
        "name": "Command Injection",
        "description": "Untrusted input passed to operating system commands.",
        "severity": "CRITICAL",
    },
    "SEC003": {
        "name": "Hardcoded Secrets",
        "description": "Passwords, API keys, or tokens hardcoded in source code.",
        "severity": "HIGH",
    },
    "SEC004": {
        "name": "Path Traversal",
        "description": "Unvalidated input used to construct file paths.",
        "severity": "HIGH",
    },
    "SEC005": {
        "name": "Insecure Deserialization",
        "description": "Unsafe deserialization of untrusted data.",
        "severity": "HIGH",
    },
    "SEC006": {
        "name": "Weak Cryptography",
        "description": "Use of weak cryptographic algorithms for security purposes.",
        "severity": "MEDIUM",
    },
    "SEC007": {
        "name": "Sensitive Data Exposure",
        "description": "Sensitive information exposed through logs or responses.",
        "severity": "HIGH",
    },
    "SEC008": {
        "name": "Missing Authorization",
        "description": "Sensitive operations lack appropriate authorization checks.",
        "severity": "HIGH",
    },
}
