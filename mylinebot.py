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
import webapp2
from google.appengine.api import urlfetch
from google.appengine.api import memcache
from urllib import urlencode
import json
import logging
from appkeys import APP_KEYS
import re
import unicodedata
from datetime import datetime, timedelta
from tzimpl import JST, UTC
from db import User, Watch, Drinking
import hmac, hashlib, base64

tz_jst = JST()
tz_utc = UTC()

usage = u'「xx時xx分から飲む」などとメッセージするとその時間の1、2、3時間後に飲み過ぎていないか確認するメッセージを送信します。\n途中で取り止めたい時、無事帰宅した時は「帰宅」や「やめ」とメッセージしてください。'
welcome = u'ようこそ！大人飲みのためのLINE Botサービスです！\n?をメッセージすると使い方を返信します。'

class BotCallbackHandler(webapp2.RequestHandler):
    def post(self):
        #params = json.loads(self.request.body.decode('utf-8'))
        params = json.loads(self.request.body)
        logging.debug('kick from line server,\n %s' % (params['result']))
        if is_valid_signature(self.request):
            eventType = params['result'][0]['eventType']
            content = params['result'][0]['content']
            if eventType == '138311609000106303':
                # received message
                receive_message(content)
            elif eventType == '138311609100106403':
                receive_operation(content)

        self.response.write(json.dumps({}))


class WatchingHandler(webapp2.RequestHandler):
    def get(self):
        watches_to_send = {}
        now = datetime.now()
        query = Drinking.query(Drinking.watches.date <= now, Drinking.watches.is_replied == False)
        drinkings_to_watch = query.fetch()
        for drinking in drinkings_to_watch:
            for i, watch in enumerate(drinking.watches):
                if watch.is_replied == False and watch.date <= now:
                    watches_to_send[drinking.mid] = { 'key' : drinking.key.id(), 'idx' : i }
                    watch.sent_count += 1
                    drinking.put()
                    break

        if len(watches_to_send):
            send_watch_message(watches_to_send)

def is_valid_signature(request):
    signature = base64.b64encode(hmac.new(APP_KEYS['line']['secret'],
                                          request.body,
                                          hashlib.sha256).digest())
    return signature == request.headers.get('X-LINE-ChannelSignature')

def has_drinking(mid):
    query = Drinking.query(Drinking.mid == mid)
    drinkings = query.fetch()
    return len(drinkings) > 0

def is_duplicated_drinking(key):
    drinking = Drinking.get_key(key).get()
    return drinking != None

def send_watch_message(watches):
    for mid, value in watches.items():
        # store sent mids for receiving replies
        logging.debug(value)
        memcache.add(mid, value, 60*5)
        
    send_message(watches.keys(), u'楽しんでいますか？どのくらい飲みましたか？返信してくださいね！')

def cancel_drinking(mid):
    query = Drinking.query(Drinking.mid == mid, Drinking.is_done == False)
    drinkings = query.fetch(keys_only=True)
    if len(drinkings) == 0:
        return u'予約された飲みはありません'

    # delete drinking entry
    Drinking.delete_drinkings(drinkings)
    return u'予約された飲みをキャンセルしました'

def receive_message(content):
    watch_info = memcache.get(content['from'])
    if watch_info is not None:
        # we think it is reply...
        memcache.delete(content['from'])
        msg = parse_reply(content['from'], content['text'], watch_info)
    else:
        msg = parse_message(content['from'], content['text'])
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


def parse_message(mid, msg):
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
    for id, elm in elms.items():
        for morphem in elm['morphemlist']:
            if morphem['pos'] == u'動詞':
                mo = re.search(u'(飲|呑|の)(み|む)', morphem['surface'])
                if mo:
                    drink_id = id
                    break
                elif morphem['surface'].find(u'やめ') >= 0:
                    cancel_id = id
                    break
        else:
            continue
        break

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
    if utc_s_date < datetime.now():
        # past date !!
        return s_date_str + u'は過去です。'

    # check duplicate
    key = mid + s_date.strftime('%Y%m%d%H%M')
    if is_duplicated_drinking(key):
        return s_date_str + u'からの飲みは登録済みです。'

    watch_counts = 3
    watch_interval = 5
    for i in range(watch_counts):
        watches.append(Watch(date=utc_s_date+timedelta(minutes=watch_interval*i+1)))

    drinking = Drinking(id=key,
                        mid=mid,
                        start_date=utc_s_date,
                        watches=watches)
    drinking.put()

    return s_date_str + u'から飲むのですね！\n約%d分毎に%d回メッセージを送信しますので、何を何杯飲んだかなど、状況を返信してくださいね。' % (watch_interval, watch_counts)

def parse_reply(mid, text, watch_info):
    drinking = Drinking.get_key(watch_info['key']).get()
    if drinking:
        drinking.watches[watch_info['idx']].reply = text
        drinking.watches[watch_info['idx']].is_replied = True
        drinking.put()
        return u'引き続き大人飲みでいきましょう！'
    else:
        return None

def send_message(mids, text):
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
