DATA_DIR = '/var/lib/pgadmin4/'
LOG_FILE = '/var/log/pgadmin4/pgadmin4.log'
SQLITE_PATH = '/var/lib/pgadmin4/pgadmin4.db'
SESSION_DB_PATH = '/var/lib/pgadmin4/sessions'
STORAGE_DIR = '/var/lib/pgadmin4/storage'
AZURE_CREDENTIAL_CACHE_DIR = '/var/lib/pgadmin4/azurecredentialcache'
KERBEROS_CCACHE_DIR = '/var/lib/pgadmin4/kerberoscache'

# TLS terminates at Caddy; pgAdmin itself is reachable only on loopback.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

