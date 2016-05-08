#! /usr/bin/env python
# -*- coding:utf-8 -*-
#
# you have to install Beautifulsoup.
# $ mkdir libs
# $ pip install -t libs beautifulsoup4


"""Callback Handler from LINE Bot platform"""

__author__ = 'ukuleletrip@gmail.com (Ukulele Trip)'

import sys
sys.path.insert(0, 'libs')
from bs4 import BeautifulSoup
from google.appengine.api import urlfetch
from urllib import urlencode
import json
import logging
from appkeys import APP_KEYS
import re
import unicodedata
from datetime import datetime, timedelta
from tzimpl import JST, UTC
from db import User, Watch, Drinking
import os

WATCH_COUNTS = 3
WATCH_INTERVAL = 60
WATCH_TIMEOUT = 10
WATCH_REPLY_TIMEOUT = 60 # 60 min
RESULT_TIMEOUT = 12*60   # 12 hour

tz_jst = JST()
tz_utc = UTC()

usage = u'大人飲みのお手伝いをします！使い方は https://ukllangel.appspot.com/help を参照してください。'
welcome = usage

def watch_drinkings():
    watches_to_send = {}
    now = datetime.now(tz=tz_utc).replace(tzinfo=None)
    query = Drinking.query(Drinking.is_done == False,
                           Drinking.watches.date <= now, Drinking.watches.is_replied == False)
    drinkings_to_watch = query.fetch()
    for drinking in drinkings_to_watch:
        for i, watch in enumerate(drinking.watches):
            if watch.is_replied == False and \
               (watch.date <= now and now <= watch.date+timedelta(minutes=WATCH_TIMEOUT)):
                watches_to_send[drinking.mid] = { 'key' : drinking.key.id(), 'idx' : i }
                watch.sent_count += 1
                drinking.put()
                break

    if len(watches_to_send):
        send_watch_message(watches_to_send)

def check_result():
    req_to_send = {}
    yesterday = (datetime.now(tz=tz_utc)+timedelta(days=-1)) \
                .replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    query = Drinking.query(Drinking.start_date >= yesterday,
                           Drinking.start_date < yesterday+timedelta(days=1),
                           Drinking.result == None)
    drinkings_to_req = query.fetch()
    for drinking in drinkings_to_req:
        req_to_send[drinking.mid] = { 'key' : drinking.key.id(), 'result' : True }
        if drinking.is_done == False:
            drinking.is_done = True
            drinking.put()

    if len(req_to_send):
        send_request_message(req_to_send)

def has_drinking(mid):
    query = Drinking.query(Drinking.mid == mid, Drinking.is_done == False)
    drinkings = query.fetch()
    return len(drinkings) > 0

def is_duplicated_drinking(key):
    drinking = Drinking.get_key(key).get()
    return drinking != None

def create_user(mid):
    user = User(id=mid)
    return user.put()

def send_request_message(reqs):
    for mid, value in reqs.items():
        # store sent mids for receiving result
        user = User.get_key(mid).get()
        if user is None:
            key = create_user(mid)
            user = key.get()

        user.status = User.STAT_WAIT_RESULT
        user.status_info = value
        user.status_expire = (datetime.now(tz=tz_utc)+timedelta(seconds=RESULT_TIMEOUT*60)) \
                              .replace(tzinfo=None)
        user.put()
        
    send_message(reqs.keys(), u'昨日はお疲れさまでした。今日の様子はいかがですか？返信してくださいね！')

def send_watch_message(watches):
    for mid, value in watches.items():
        # store sent mids for receiving replies
        user = User.get_key(mid).get()
        if user is None:
            key = create_user(mid)
            user = key.get()

        user.status = User.STAT_WAIT_REPLY
        user.status_info = value
        user.status_expire = (datetime.now(tz=tz_utc)+timedelta(seconds=WATCH_REPLY_TIMEOUT*60)) \
                              .replace(tzinfo=None)
        user.put()
        
    send_message(watches.keys(), u'楽しんでいますか？どのくらい飲みましたか？返信してくださいね！')

def cancel_drinking(mid):
    query = Drinking.query(Drinking.mid == mid, Drinking.is_done == False)
    drinkings = query.fetch(keys_only=True)
    if len(drinkings) == 0:
        return u'予約された飲みはありません'

    # delete drinking entry
    Drinking.delete_drinkings(drinkings)
    return u'予約された飲みをキャンセルしました'

def finish_the_drinkig(drinking):
    drinking.is_done = True
    drinking.finished_date = datetime.now(tz=tz_utc).replace(tzinfo=None)
    drinking.put()
    return u'お疲れさまでした！'

def finish_drinking(mid):
    query = Drinking.query(Drinking.mid == mid, Drinking.is_done == False)
    drinkings = query.fetch(1)
    if drinkings == None:
        return u'すでに帰宅されているようです'

    # finish the drinking
    return finish_the_drinkig(drinkings[0])

def get_status(mid, is_peek=False):
    now = datetime.now(tz=tz_utc).replace(tzinfo=None)
    user = User.get_key(mid).get()
    if user is None or user.status == User.STAT_NONE:
        return (User.STAT_NONE, None)
    else:
        status = user.status
        expire = user.status_expire
        info = user.status_info

        if is_peek == False:
            user.status = User.STAT_NONE
            user.put()

        if expire < now:
            # expired
            return (User.STAT_NONE, None)

        return (status, info)

def receive_message(content):
    (status, info) = get_status(content['from'])
    logging.debug('status: %d' % (status))
    if status == User.STAT_WAIT_REPLY:
        # we think it is reply...
        msg = handle_reply(content['from'], content['text'], info)
    elif status == User.STAT_WAIT_RESULT:
        msg = handle_result(content['from'], content['text'], info)
    else:
        msg = handle_message(content['from'], content['text'])
        if msg is None:
            msg = usage

    if msg:
        send_message([content['from']], msg)

def receive_operation(content):
    opType = int(content['opType'])
    if opType == 4:
        # add as friend
        send_message([content['params'][0]], welcome)
    elif opType == 8:
        # block account
        pass

def depends_drink(id, elms, nomi_id):
    elm = elms[id]
    while elm['dependency'] >= 0:
        if elm['dependency'] == nomi_id:
            return True
        elm = elms[elm['dependency']]
    return False

        
def call_yahoo_jparser(msg):
    url = 'http://jlp.yahooapis.jp/DAService/V1/parse'

    result = urlfetch.fetch(
        url=url,
	method=urlfetch.POST, 
	headers={'Content-Type':'application/x-www-form-urlencoded'},
	payload=urlencode({
	    'appid'    : APP_KEYS['yahoo']['id'],
	    'sentence' : msg.encode('utf-8')})
	)
    logging.debug(result.content)
    return BeautifulSoup(result.content.replace('\n',''), 'html.parser')


def parse_message(msg):
    soup = call_yahoo_jparser(msg)

    # 1st, create parsed dict with key=id
    elms = {}
    for chunk in soup.resultset.result.chunklist:
        elm = {}
        elms[int(chunk.id.text)] = elm
        elm['dependency'] = int(chunk.dependency.text)
        elm['morphemlist'] = []
        for morphem in chunk.morphemlist:
            elm['morphemlist'].append({ 'surface':morphem.surface.text,
                                        'pos'    :morphem.pos.text })

    # 2nd, find drink or cancel morphem
    drink_id = -1
    cancel_id = -1
    finished_id = -1
    # I'm not sure that I have to use Yahoo API...
    # I just wanted to use that !!!
    for id, elm in elms.items():
        for morphem in elm['morphemlist']:
            if morphem['pos'] == u'動詞' or morphem['pos'] == u'名詞':
                mo = re.search(u'(飲|呑|の)(み|む)', morphem['surface'])
                if mo:
                    drink_id = id
                    break
                elif morphem['surface'].find(u'やめ') >= 0:
                    cancel_id = id
                    break
                elif (morphem['surface'].find(u'終') >= 0 or
                      morphem['surface'].find(u'帰') >= 0):
                    finished_id = id
                    break
        else:
            continue
        break

    return (elms, drink_id, cancel_id, finished_id)

def handle_message(mid, msg):
    (elms, drink_id, cancel_id, finished_id) = parse_message(msg)

    if finished_id >= 0:
        # this is finish message
        return finish_drinking(mid)

    if cancel_id >= 0:
        # this is cancel message
        return cancel_drinking(mid)

    if drink_id == -1:
        # this is not nomi message...
        return None

    # user can have only one drink
    if has_drinking(mid):
        return u'飲みは1つしか予約できません。予約した飲みをキャンセルするには「やめ」とメッセージしてください。'

    # 3rd, determine date and time
    s_date = datetime.now(tz=tz_jst).replace(second=0, microsecond=0)
    for id, elm in elms.items():
        if depends_drink(id, elms, drink_id):
            start_info = ''
            for morphem in elm['morphemlist']:
                start_info += morphem['surface']
            start_info = unicodedata.normalize('NFKC', start_info)

            # find time
            time_patterns = [
                '(\d\d)\D(\d\d)\D',
                '(\d\d)(\d\d)',
                '(\d+)\D(\d+)',
                u'(\d+)時'
            ]     
            for pattern in time_patterns:
                mo = re.search(pattern, start_info)
                if mo:
                    hour = int(mo.group(1))
                    minute = int(mo.group(2)) if mo.lastindex == 2 else 0
                    if hour < 12 and \
                       (start_info.find('PM') >= 0 or
                        start_info.find('pm') >= 0 or
                        start_info.find(u'午後') >= 0):
                        hour += 12
                    s_date = s_date.replace(hour=hour, minute=minute)
                    break

            # find date
            if start_info.find(u'明日') >= 0:
                s_date = s_date + timedelta(days=1)
            elif start_info.find(u'明後日') >= 0 or \
                 start_info.find(u'あさって') >= 0:
                s_date = s_date + timedelta(days=2)
            else:
                date_patterns = [
                    u'(\d+)月(\d+)',
                    '(\d+)/(\d+)'
                ]
                for pattern in date_patterns:
                    mo = re.search(pattern, start_info)
                    if mo:
                        month = int(mo.group(1))
                        day = int(mo.group(2))
                        s_date = s_date.replace(month=month, day=day)
                        break

    # store data
    watches = []
    utc_s_date = s_date.astimezone(tz_utc).replace(tzinfo=None)
    s_date_str = u'%d月%d日%d時%d分' % (s_date.month, s_date.day, s_date.hour, s_date.minute)

    # check if s_date is valid
    if utc_s_date < datetime.now(tz=tz_utc).replace(tzinfo=None):
        # past date !!
        return s_date_str + u'は過去です。'

    # check duplicate
    key = mid + s_date.strftime('%Y%m%d%H%M')
    if is_duplicated_drinking(key):
        return s_date_str + u'からの飲みは登録済みです。'

    for i in range(WATCH_COUNTS):
        watches.append(Watch(date=utc_s_date+timedelta(minutes=WATCH_INTERVAL*(i+1))))

    drinking = Drinking(id=key,
                        mid=mid,
                        start_date=utc_s_date,
                        watches=watches)
    drinking.put()

    return s_date_str + u'から飲むのですね！\n約%d分毎に%d回メッセージを送信しますので、何を何杯飲んだかなど、状況を返信してくださいね。' % (WATCH_INTERVAL, WATCH_COUNTS)

def handle_result(mid, text, info):
    drinking = Drinking.get_key(info['key']).get()
    if drinking:
        drinking.result = text
        drinking.put()
        return u'次回も大人飲みのお手伝いをします。またメッセージくださいね！'
    else:
        return None

def handle_reply(mid, text, watch_info):
    drinking = Drinking.get_key(watch_info['key']).get()
    if drinking:
        drinking.watches[watch_info['idx']].reply = text
        drinking.watches[watch_info['idx']].is_replied = True
        if watch_info['idx'] == WATCH_COUNTS-1 or \
           (text.find(u'帰宅') >= 0 or text.find(u'終') >= 0):
            # it is last watch
            msg = finish_the_drinkig(drinking)
        else:
            msg = u'引き続き大人飲みでいきましょう！'
            drinking.put()
        return msg
    else:
        return None

def send_message(mids, text):
    if 'SERVER_SOFTWARE' not in os.environ or \
       os.environ['SERVER_SOFTWARE'].find('testbed') >= 0:
        # I'm not running on server
        return

    if len(mids) == 0:
        return
        
    url = 'https://trialbot-api.line.me/v1/events'
    params = {
        'to': mids,
        'toChannel': 1383378250,
        'eventType': '138311608800106203',
        'content' : {
            'contentType' : 1,
            'toType'      : 1,
            'text'        : text
        }
    }
    data = json.dumps(params, ensure_ascii=False)
    logging.debug(data)
    result = urlfetch.fetch(
        url=url,
        payload=data,
        method=urlfetch.POST,
        headers={
            'Content-type':'application/json; charset=UTF-8',
            'X-Line-ChannelID': APP_KEYS['line']['id'],
            'X-Line-ChannelSecret': APP_KEYS['line']['secret'],
            'X-Line-Trusted-User-With-ACL': APP_KEYS['line']['mid']
        }
    )
    if result.status_code == 200:
        logging.debug(result.content)
    else:
        logging.error(result.content)

