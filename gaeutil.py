#! /usr/bin/env python
# -*- coding:utf-8 -*-

import os
from datetime import datetime, timedelta
from tzimpl import JST, UTC

tz_jst = JST()
tz_utc = UTC()


def is_on_local_server():
    return 'SERVER_SOFTWARE' not in os.environ or \
        os.environ['SERVER_SOFTWARE'].find('testbed') >= 0

# following functions are for GAE UTC work around.
def utc_now():
    return datetime.now(tz=tz_utc).replace(tzinfo=None)

def just_minute(dt):
    return dt.replace(second=0, microsecond=0)

def utc_to_jst(dt):
    return dt.replace(tzinfo=tz_utc).astimezone(tz_jst)

def jst_to_utc(dt):
    return dt.replace(tzinfo=tz_jst).astimezone(tz_utc)

