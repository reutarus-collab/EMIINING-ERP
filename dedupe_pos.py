import shutil, sys
P = 'services/pos_service.py'
s = open(P, encoding='utf-8').read()
blocks = [
    "import math\n",
    "from flask import g\n",
    "# Max discount as % of the bill, per role. A role not listed gets 0%.\nDISCOUNT_CAP_PCT = {'sales': 5.0, 'accountant': 10.0, 'admin': 30.0}\n",
    'def _num(value, name):\n    try:\n        v = float(value)\n    except (TypeError, ValueError):\n        raise Exception(f"Invalid {name}.")\n    if not math.isfinite(v):\n        raise Exception(f"Invalid {name}.")\n    return v\n',
]
for b in blocks:
    first = s.find(b)
    if first == -1:
        print('NOT FOUND (left alone):', b.splitlines()[0])
        continue
    cut = first + len(b)
    n = s[cut:].count(b)
    s = s[:cut] + s[cut:].replace(b, '')
    print('removed', n, 'duplicate(s) of:', b.splitlines()[0])
if s.count('def _num(value, name):') != 1:
    sys.exit('Still not exactly one _num definition. Nothing changed.')
shutil.copy(P, P + '.bak')
open(P, 'w', encoding='utf-8', newline='\n').write(s)
print('Cleaned', P)
