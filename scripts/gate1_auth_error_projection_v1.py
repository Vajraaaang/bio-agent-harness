"""Bounded RAM-only projection of pinned public device-auth error templates.

No I/O, logging, subprocesses, hashes, secrets, authentication or network. Raw
buffers are cleared best-effort; Python copies are not cryptographically erased.
"""
import re
LIMIT_BYTES=8192
FORMAT='devbench.auth-error-projection/1'
RECEIPT_FIELDS=frozenset({'format','stage_enum','category_enum','http_status_bucket_or_null','oauth_label_or_null','classification_complete','raw_retained','raw_hashed'})
CAPTURE_FIELDS=frozenset({'stderr_bytes_seen','buffered_bytes_peak','bound_exceeded','rejected_overshoot_bytes'})
STAGES=frozenset({'startup','device_request','poll','exchange','policy','io','persistence','unknown'})
CATEGORIES=frozenset({'configuration','login_disallowed','device_code_unsupported','http_error','transport_error','poll_timeout','workspace_restricted','persistence_task_failed','errno_permission','errno_readonly','errno_missing','errno_no_space','errno_io','unknown'})
HTTP_BUCKETS=frozenset({'401','403','404','429','other_4xx','5xx','other_non_success'})
OAUTH_LABELS=frozenset({'invalid_grant','temporarily_unavailable','proxy_auth_required'})
DEVICE_PREFIX='Error logging in with device code: '
_HTTP_PREFIXES=(('device code request failed with status ','device_request'),('device auth failed with status ','poll'),('device code exchange failed: token endpoint returned status ','exchange'))
_SGR=re.compile(r'\x1b\[[0-9;]{0,32}m')
_STATUS=re.compile(r'([1-5][0-9]{2})(?=$|[ :])')
_ERRNO={'Permission denied (os error 13)':'errno_permission','Operation not permitted (os error 1)':'errno_permission',
    'Read-only file system (os error 30)':'errno_readonly','No such file or directory (os error 2)':'errno_missing',
    'No space left on device (os error 28)':'errno_no_space','Input/output error (os error 5)':'errno_io'}


def _receipt(stage='unknown',category='unknown',bucket=None,oauth=None):
    return {'format':FORMAT,'stage_enum':stage,'category_enum':category,'http_status_bucket_or_null':bucket,
        'oauth_label_or_null':oauth,'classification_complete':category!='unknown','raw_retained':False,'raw_hashed':False}


def _plain(raw):
    try:text=raw.decode('utf-8')
    except UnicodeDecodeError:return None
    text=_SGR.sub('',text).replace('\r\n','\n')
    if '\x1b'in text or any((ord(c)<32 and c not in '\n\t')or ord(c)==127 for c in text):return None
    return text


def _classify(raw):
    text=_plain(raw)
    if not text or not text.endswith('\n'):return _receipt()
    lines=text.split('\n')
    for index,line in enumerate(lines):
        if line.startswith(('Error loading configuration: ','Error parsing -c overrides: ')):
            return _receipt('startup','configuration')
        if line=='ChatGPT login is disabled. Use API key login instead.':return _receipt('startup','login_disallowed')
        if not line.startswith(DEVICE_PREFIX):continue
        # Select the FIRST CLI error frame. Never reinterpret later body lines.
        tail='\n'.join(lines[index:])[len(DEVICE_PREFIX):][:-1]  # Remove ONE renderer newline only.
        for prefix,stage in _HTTP_PREFIXES:
            if not tail.startswith(prefix):continue
            remainder=tail[len(prefix):];match=_STATUS.match(remainder)
            if match is None:return _receipt()
            code=int(match[1])
            if 200<=code<300:return _receipt()
            bucket=str(code)if code in [401,403,404,429]else('other_4xx'if 400<=code<500 else'5xx'if code>=500 else'other_non_success')
            oauth=None
            if stage=='exchange':
                _head,separator,detail=remainder[match.end():].partition(': ')
                if separator and detail in OAUTH_LABELS:oauth=detail
            return _receipt(stage,'http_error',bucket,oauth)
        if tail=='device code login is not enabled for this Codex server. Use the browser login or verify the server URL.':
            return _receipt('device_request','device_code_unsupported')
        if tail=='device auth timed out after 15 minutes':return _receipt('poll','poll_timeout')
        if tail.startswith('device code exchange failed: error sending request for url ('):
            return _receipt('exchange','transport_error')
        if tail.startswith('error sending request for url ('):return _receipt('unknown','transport_error')
        if tail.startswith('Login is restricted to workspace id(s) ')or tail=='Login is restricted to a specific workspace, but the token did not include an chatgpt_account_id claim.':
            return _receipt('policy','workspace_restricted')
        if tail.startswith('persist task failed: '):return _receipt('persistence','persistence_task_failed')
        if tail in _ERRNO:return _receipt('io',_ERRNO[tail])
        return _receipt()
    return _receipt()


class AuthErrorProjection:
    """One bounded stderr collector; no raw text escapes this object."""
    def __init__(self):
        self._buffer=bytearray();self._seen=0;self._peak=0;self._exceeded=False;self._overshoot=0
        self._closed=False;self._result=None
    def feed(self,data):
        if self._closed:raise ValueError('projection_already_closed')
        if type(data)is not bytes:raise TypeError('bytes_only_projection_input')
        self._seen+=len(data)
        if self._exceeded:return
        if self._seen>LIMIT_BYTES:
            self._exceeded=True;self._overshoot=self._seen-LIMIT_BYTES;self._buffer.clear();return
        self._buffer.extend(data);self._peak=max(self._peak,len(self._buffer))
    def capture_stats(self):
        return {'stderr_bytes_seen':self._seen,'buffered_bytes_peak':self._peak,'bound_exceeded':self._exceeded,'rejected_overshoot_bytes':self._overshoot}
    def finish(self,*,input_complete=True):
        if type(input_complete)is not bool:raise TypeError('boolean_completion_required')
        if self._result is not None:return dict(self._result)
        self._result=_classify(bytes(self._buffer))if input_complete and not self._exceeded and not self._closed else _receipt()
        self._buffer.clear();self._closed=True;return dict(self._result)
    def discard(self):
        self._buffer.clear();self._closed=True;self._result=_receipt()
