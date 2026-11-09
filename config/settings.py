import os
import secrets
from pathlib import Path
from urllib.parse import urlparse, unquote

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv('DEBUG', '0') == '1'
SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError('Set SECRET_KEY before starting. For local development set DEBUG=1.')
    SECRET_KEY = 'development-only-' + secrets.token_hex(32)
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
if os.getenv('RENDER_EXTERNAL_HOSTNAME'):
    ALLOWED_HOSTS.append(os.environ['RENDER_EXTERNAL_HOSTNAME'])
INSTALLED_APPS = ['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes',
                  'django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles','axes','warehouse']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware','django.contrib.sessions.middleware.SessionMiddleware',
              'django.middleware.common.CommonMiddleware','django.middleware.csrf.CsrfViewMiddleware',
              'django.contrib.auth.middleware.AuthenticationMiddleware','django.contrib.messages.middleware.MessageMiddleware',
              'django.middleware.clickjacking.XFrameOptionsMiddleware','axes.middleware.AxesMiddleware']
AUTHENTICATION_BACKENDS=['axes.backends.AxesStandaloneBackend','django.contrib.auth.backends.ModelBackend']
AXES_FAILURE_LIMIT=5
AXES_COOLOFF_TIME=0.25
AXES_LOCKOUT_PARAMETERS=['username']
AXES_RESET_ON_SUCCESS=True
# Per-account limits avoid locking all warehouse staff behind the shared proxy.
# Also configure client-IP rate limiting at the hosting edge before public rollout.
SILENCED_SYSTEM_CHECKS=['axes.W006']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],
 'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request',
 'django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
if os.getenv('DATABASE_URL'):
    d = urlparse(os.environ['DATABASE_URL'])
    DATABASES = {'default':{'ENGINE':'django.db.backends.postgresql','NAME':unquote(d.path[1:]),
      'USER':unquote(d.username or ''),'PASSWORD':unquote(d.password or ''),'HOST':d.hostname,'PORT':d.port or 5432,
      'OPTIONS':{'sslmode':os.getenv('DB_SSLMODE','require')}}}
elif DEBUG:
    DATABASES = {'default':{'ENGINE':'django.db.backends.sqlite3','NAME':BASE_DIR/'dev.sqlite3','OPTIONS':{'timeout':20}}}
else:
    raise RuntimeError('Production requires PostgreSQL DATABASE_URL.')
AUTH_PASSWORD_VALIDATORS = [
 {'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
 {'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator'},
 {'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},
 {'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE='en-us'
TIME_ZONE='America/Chicago'
USE_I18N=True
USE_TZ=True
STATIC_URL='/static/'
STATIC_ROOT=BASE_DIR/'staticfiles'
STATICFILES_DIRS=[BASE_DIR/'static']
STORAGES={'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
 'staticfiles':{'BACKEND':'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
LOGIN_URL='/login/'
LOGIN_REDIRECT_URL='/'
LOGOUT_REDIRECT_URL='/login/'
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE='Lax'
SECURE_CONTENT_TYPE_NOSNIFF=True
X_FRAME_OPTIONS='DENY'
CSRF_TRUSTED_ORIGINS=[s for s in os.getenv('CSRF_TRUSTED_ORIGINS','').split(',') if s]
if not DEBUG:
    SESSION_COOKIE_SECURE=True
    CSRF_COOKIE_SECURE=True
    SECURE_SSL_REDIRECT=True
    SECURE_HSTS_SECONDS=31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS=True
    # Set only behind a trusted proxy that strips client-supplied forwarding headers.
    if os.getenv('TRUST_PROXY','0')=='1':
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')

# Configure a company-approved SMTP sender before enabling customer notifications.
WMS_EMAIL_ENABLED=os.getenv('WMS_EMAIL_ENABLED','0')=='1'
WMS_RECEIPT_EMAIL_ENABLED=os.getenv('WMS_RECEIPT_EMAIL_ENABLED','0')=='1'
DATA_UPLOAD_MAX_NUMBER_FILES=12
FILE_UPLOAD_MAX_MEMORY_SIZE=1024*1024
EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST=os.getenv('EMAIL_HOST','')
EMAIL_PORT=int(os.getenv('EMAIL_PORT','587'))
EMAIL_HOST_USER=os.getenv('EMAIL_HOST_USER','')
EMAIL_HOST_PASSWORD=os.getenv('EMAIL_HOST_PASSWORD','')
EMAIL_USE_TLS=os.getenv('EMAIL_USE_TLS','1')=='1'
EMAIL_TIMEOUT=20
DEFAULT_FROM_EMAIL=os.getenv('DEFAULT_FROM_EMAIL','')
