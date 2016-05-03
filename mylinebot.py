#! /usr/bin/env python
# -*- coding:utf-8 -*-

"""Callback Handler from LINE Bot platform"""

__author__ = 'ukuleletrip@gmail.com (Ukulele Trip)'

import sys
sys.path.insert(0, 'libs')
from bs4 import BeautifulSoup
import webapp2
from google.appengine.api import urlfetch
from urllib import urlencode
import json
import logging
from appkeys import APP_KEYS
import re
import unicodedata
from datetime import datetime, timedelta
from tzimpl import JST, UTC

tz_jst = JST()
tz_utc = UTC()
usage = u'「xx時xx分から飲む」などとメッセージするとその時間の1、2、3時間後に飲み過ぎていないか確認するメッセージを送信します。\n途中で取り止めたい時、無事帰宅した時は「帰宅」や「やめ」とメッセージしてください。'

class BotCallbackHandler(webapp2.RequestHandler):
    def post(self):
        #params = json.loads(self.request.body.decode('utf-8'))
        params = json.loads(self.request.body)
        logging.debug('kick from line server,\n %s' % (params['result']))

        content = params['result'][0]['content']

        msg = parse_message(content['text'])
        if msg is None:
            msg = usage
        
        send_message(content['from'], msg)
        self.response.write(json.dumps({}))

def depends_nomi(id, elms, nomi_id):
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

    # 2nd, find nomi morphem
    nomi_id = -1
    for id, elm in elms.items():
        for morphem in elm['morphemlist']:
            if morphem['pos'] == u'動詞':
                mo = re.search(u'(飲|呑|の)(み|む)', morphem['surface'])
                if mo:
                    nomi_id = id
                    break
        else:
            continue
        break

    if nomi_id == -1:
        # this is not nomi message...
        return None

    # 3rd, determine date and time
    s_date = datetime.now(tz=tz_jst).replace(second=0, microsecond=0)
    for id, elm in elms.items():
        if depends_nomi(id, elms, nomi_id):
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

    return u'%d月%d日%d時%d分から飲むのですね！' % (s_date.month, s_date.day,
                                                    s_date.hour, s_date.minute)


def send_message(to, text):
    url = 'https://trialbot-api.line.me/v1/events'
    params = {
        'to': [ to ],
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
