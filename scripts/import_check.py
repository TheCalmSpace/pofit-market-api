import os
import sys
import traceback

sys.path.append(r'f:/Pofit/pofit-market-api')
# Minimal env to allow imports that read config
os.environ.setdefault('SUPABASE_URL', 'https://example.test')
os.environ.setdefault('SUPABASE_SERVICE_ROLE_KEY', 'testkey')

modules = [
    'app.repositories.alpha_history_repository',
    'app.repositories.portfolio_performance_repository',
    'app.repositories.alpha_portfolio_repository',
    'app.services.alpha_portfolio_service',
    'app.services.performance_service',
    'app.routers.alpha',
]

failed = False
for m in modules:
    try:
        __import__(m, fromlist=['*'])
        print('OK:', m)
    except Exception as e:
        failed = True
        print('FAIL:', m, '->', type(e).__name__, e)
        traceback.print_exc()

if failed:
    sys.exit(1)

print('IMPORTS_OK')
