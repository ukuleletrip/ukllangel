#! /usr/bin/env python
# -*- coding:utf-8 -*-
#

import sys
sys.path.insert(0, 'libs')
from google.appengine.api import urlfetch
from unicodedata import normalize
from urllib import urlencode
from appkeys import APP_KEYS
from datetime import datetime, timedelta
import re
from gaeutil import *
from bs4 import BeautifulSoup

class Message(object):
    # type of message
    NO_MEANING = 0
    DECLARE_DRINKING = 1
    REPORT_ALCOHOL = 2
    CANCEL_DRINKING = 3
    FINISH_DRINKING = 4
    REQUEST_HISTORY = 5

    def __init__(self, text):
        self.text = text
        self.type = self.NO_MEANING
        self.param = None
        self._parse_text(text)

    def _parse_text(self, text):
        if type(text) == unicode:
            text = normalize('NFKC', text)
        soup = _call_yahoo_jparser(text, 'DA')

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
        (RE, FIND) = range(2)
        meanings = (DRINK, DRANK, CANCEL, FINISH, HISTORY) = range(5)
        result = [-1]*len(meanings)
        parsers = [
            { 'type' : RE,   'word' : u'(飲|呑|の)(み|む)', 'meaning' : DRINK, 'break' : True },
            #{ 'type' : RE,   'word' : u'(飲|呑|の)(ん)',    'meaning' : DRANK, 'break' : True },
            { 'type' : FIND, 'word' : u'やめ',             'meaning' : CANCEL, 'break' : True },
            { 'type' : FIND, 'word' : u'終',              'meaning' : FINISH, 'break' : True },
            { 'type' : FIND, 'word' : u'帰',              'meaning' : FINISH, 'break' : True },
            { 'type' : FIND, 'word' : u'過去',             'meaning' : HISTORY,'break' : False },
            { 'type' : FIND, 'word' : u'前',              'meaning' : HISTORY, 'break' : False },
            { 'type' : RE,   'word' : u'(今|これ)まで',    'meaning' : HISTORY, 'break' : False },
        ]

        # parsing plain text may be better than using Japanese parser API...
        for id, elm in elms.items():
            for morphem in elm['morphemlist']:
                if morphem['pos'] != u'動詞' and morphem['pos'] != u'名詞' and morphem['pos'] != u'副詞':
                    continue

                for parser in parsers:
                    if parser['type'] == RE:
                        mo = re.search(parser['word'], morphem['surface'])
                        if mo is None:
                            continue

                        result[parser['meaning']] = id
                        if parser['break']:
                            break

                    elif parser['type'] == FIND:
                        if morphem['surface'].find(parser['word']) < 0:
                            continue

                        result[parser['meaning']] = id
                        if parser['break']:
                            break
                else:
                    # next morphem
                    continue
                break

            else:
                # next element
                continue
            break

        if result[HISTORY] >= 0 and result[DRINK] >= 0 and _depends_drink(result[HISTORY],
                                                                          elms,
                                                                          result[DRINK]):
            self.type = self.REQUEST_HISTORY
            return
        elif result[FINISH] >= 0:
            self.type = self.FINISH_DRINKING
            return
        elif result[CANCEL] >= 0:
            self.type = self.CANCEL_DRINKING
            return
        elif result[DRINK] < 0:
            self.type = self.NO_MEANING
            return

        self.type = self.DECLARE_DRINKING
        self.param = self._parse_start_date(elms, result[DRINK])


    def _parse_start_date(self, elms, drink_id):
        s_date = just_minute(datetime.now())
        for id, elm in elms.items():
            if _depends_drink(id, elms, drink_id):
                start_info = ''
                for morphem in elm['morphemlist']:
                    start_info += morphem['surface']

                # find time
                time_patterns = [
                    u'(\d\d)[時じ:：](\d\d)\D',
                    '(\d\d)(\d\d)',
                    u'(\d+)[時じ:：](\d+)',
                    u'(\d+)[時じ]'
                ]     
                for pattern in time_patterns:
                    mo = re.search(pattern, start_info)
                    if mo:
                        hour = int(mo.group(1))
                        minute = int(mo.group(2)) if mo.lastindex == 2 else 0
                        if hour < 12 and \
                           (start_info.find('PM') >= 0 or
                            start_info.find('pm') >= 0 or
                            start_info.find(u'午後') >= 0 or
                            start_info.find(u'ごご') >= 0):
                            hour += 12
                        s_date = s_date.replace(hour=hour, minute=minute)
                        break

                # find date
                if start_info.find(u'明日') >= 0 or \
                   start_info.find(u'あした') >= 0 or \
                   start_info.find(u'あす') >= 0:
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

        return s_date

    def parse_drinking_amount(self, amount=None):
        msg = self.text.translate({
            ord(u'一'): u'1',
            ord(u'二'): u'2',
            ord(u'三'): u'3',
            ord(u'四'): u'4',
            ord(u'五'): u'5',
            ord(u'六'): u'6',
            ord(u'七'): u'7',
            ord(u'八'): u'8',
            ord(u'九'): u'9'
        }) 
        if type(msg) == unicode:
            msg = normalize('NFKC', msg)
        soup = _call_yahoo_jparser(msg, 'MA')
        elms = []
        for word in soup.resultset.ma_result.word_list:
            elms.append({ 'surface':word.surface.text,
                          'pos'    :word.pos.text })

        prev_noun = None
        if amount is None:
            amount = {}
        for elm in elms:
            if elm['pos'] == u'名詞':
                if elm['surface'].isdigit():
                    if prev_noun:
                        num = amount.get(prev_noun['surface'], 0)
                        amount[prev_noun['surface']] = num + int(elm['surface'])
                        prev_noun = None
                else:
                    prev_noun = elm

        return amount


def _call_yahoo_jparser(msg, ptype):
    url = 'http://jlp.yahooapis.jp/%sService/V1/parse' % (ptype)

    params = urlencode({ 'appid'    : APP_KEYS['yahoo']['id'],
                         'sentence' : msg.encode('utf-8')})

    if is_on_local_server:
        import urllib2
        results = urllib2.urlopen(url, params)
        return BeautifulSoup(results.read().replace('\n',''), 'html.parser')
    else:
        result = urlfetch.fetch(
            url=url,
            method=urlfetch.POST, 
            headers={'Content-Type':'application/x-www-form-urlencoded'},
            payload=params)

        logging.debug(result.content)
        return BeautifulSoup(result.content.replace('\n',''), 'html.parser')


def _depends_drink(id, elms, nomi_id):
    elm = elms[id]
    while elm['dependency'] >= 0:
        if elm['dependency'] == nomi_id:
            return True
        elm = elms[elm['dependency']]
    return False









       
