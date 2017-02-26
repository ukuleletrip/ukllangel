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
import uuid
import jinja2
from linebotapi import LineBotAPI, WebhookRequest
from googleapiclient import discovery
from oauth2client.client import GoogleCredentials


JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

WATCH_COUNTS = 5
WATCH_INTERVAL = 60
WATCH_TIMEOUT = 20
WATCH_REPLY_TIMEOUT = 60 # 60 min
RESULT_TIMEOUT = 12*60   # 12 hour
HISTORY_DURATION = 30
MAX_HISTORY = 20

tz_jst = JST()
tz_utc = UTC()

service_url = 'https://ukllangel.appspot.com'

usage = u'大人飲みのお手伝いをします！使い方は %s/help を参照してください。' % (service_url)
welcome = usage

def utc_now():
    return datetime.now(tz=tz_utc).replace(tzinfo=None)

def format_jdate(dt):
    weekday = (u'月',u'火',u'水',u'木',u'金',u'土',u'日')
    return "%d/%d/%d(%s) %02d:%02d" % (dt.year, dt.month, dt.day, weekday[dt.weekday()],
                                       dt.hour, dt.minute)

def generate_random_url(mid):
    for i in range(5):
        history_url = uuid.uuid4().hex
        query = User.query(User.history_url == history_url)
        users = query.fetch()
        if len(users) > 0:
            # duplicated url...
            continue
        break
    else:
        # I believe it will not happen
        return None
    
    return history_url

def is_on_local_server():
    return 'SERVER_SOFTWARE' not in os.environ or \
        os.environ['SERVER_SOFTWARE'].find('testbed') >= 0

def get_drinking_quality_word(sentiment, magnitude):
    if sentiment < -0.1:
        return u'飲みすぎたようですね。'
    elif sentiment < 0.1:
        return u'少し飲みすぎたようですね。'
    else:
        return u'大人飲みできたようですね。'

def watch_drinkings():
    watches_to_send = {}
    now = utc_now()
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
    yesterday = (utc_now()+timedelta(days=-1)).replace(hour=0, minute=0, second=0, microsecond=0)
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
        user.status_expire = utc_now()+timedelta(seconds=RESULT_TIMEOUT*60)
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
        user.status_expire = utc_now()+timedelta(seconds=WATCH_REPLY_TIMEOUT*60)
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
    drinking.finished_date = utc_now()
    drinking.put()
    return u'お疲れさまでした！'

def finish_drinking(mid):
    query = Drinking.query(Drinking.mid == mid, Drinking.is_done == False)
    drinkings = query.fetch(1)
    if len(drinkings) == 0:
        return u'すでに帰宅されているようです'

    # finish the drinking
    return finish_the_drinkig(drinkings[0])

def get_drinking_history(mid, num=MAX_HISTORY):
    dt = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    query = Drinking.query(Drinking.mid==mid, Drinking.start_date<dt).order(-Drinking.start_date)
    return query.fetch(num)

def get_drinking_history_content(history_url):
    query = User.query(User.history_url==history_url)
    users = query.fetch(1)
    if len(users) == 0:
        return None

    drinkings = get_drinking_history(users[0].key.id())
    t_drinkings = []
    for drinking in drinkings:
        t_drinking = {}
        t_drinking['started'] = format_jdate(drinking.start_date.
                                             replace(tzinfo=tz_utc).astimezone(tz_jst))
        t_drinking['finished'] = drinking.finished_date. \
                                 replace(tzinfo=tz_utc).astimezone(tz_jst).strftime('%H:%M') \
                                 if drinking.finished_date else u''
        t_drinking['result'] = drinking.result if drinking.result else u''
        t_drinking['watches'] = []
        for watch in drinking.watches:
            if watch.sent_count > 0:
                t_watch = {}
                t_watch['date'] = watch.date. \
                                  replace(tzinfo=tz_utc).astimezone(tz_jst).strftime('%H:%M')
                t_watch['sent_count'] = watch.sent_count
                t_watch['reply'] = watch.reply if watch.reply else u'返信なし'
                t_drinking['watches'].append(t_watch)
        t_drinkings.append(t_drinking)

    template_values = {
        'drinkings': t_drinkings,
    }
    template = JINJA_ENVIRONMENT.get_template('templates/index.html')
    return template.render(template_values)

def get_worst_dinking(mid):
    query = Drinking.query(Drinking.mid==mid, Drinking.is_done==True).order(Drinking.sentiment)
    drinkings = query.fetch(1)
    return drinkings[0] if len(drinkings) > 0 else None

def history_drinking(mid):
    worst_drinking = get_worst_dinking(mid)

    history_url = generate_random_url(mid)
    user = User.get_key(mid).get()
    if history_url is None or user is None:
        return u'まだ飲みの登録がないか、参照が行えません'

    user.history_url = history_url
    user.history_expire = utc_now()+timedelta(minutes=5)
    user.put()

    msg = ''
    if worst_drinking:
        msg += u'最悪の飲みは %s だったようです。\n' % \
               (worst_drinking.start_date.
                replace(tzinfo=tz_utc).astimezone(tz_jst).strftime('%Y-%m-%d'))
        for kind in worst_drinking.summary:
            msg += u'  %s %d 杯\n' % (kind, worst_drinking.summary[kind])
        msg += '\n'

    url = service_url + '/history/' + history_url
    return msg + u'過去の飲みは %s を参照ください。このURLは%d分間有効です。' % (url, HISTORY_DURATION)

def get_status(user_id, is_peek=False):
    mid = user_id
    now = utc_now()
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

def receive_message(recv_req):
    user_id = recv_req.get_user_id()
    reply_token = recv_req.get_reply_token()
    (status, info) = get_status(user_id)
    logging.debug('status: %d' % (status))

    in_msg = recv_req.get_message()
    if status == User.STAT_WAIT_REPLY:
        # we think it is reply...
        msg = handle_reply(in_msg, info)
    elif status == User.STAT_WAIT_RESULT:
        msg = handle_result(in_msg, info)
    else:
        msg = handle_message(user_id, in_msg)
        if msg is None:
            msg = usage

    if msg:
        reply_message(reply_token, msg)

def receive_follow(recv_req):
    # add as friend
    reply_message(recv_req.get_reply_token(), welcome)

def depends_drink(id, elms, nomi_id):
    elm = elms[id]
    while elm['dependency'] >= 0:
        if elm['dependency'] == nomi_id:
            return True
        elm = elms[elm['dependency']]
    return False

        
def call_yahoo_jparser(msg, ptype):
    url = 'http://jlp.yahooapis.jp/%sService/V1/parse' % (ptype)

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


def call_google_sentiment_analytics(msg):
    if is_on_local_server():
        return 0.0, 0.0

    credentials = GoogleCredentials.get_application_default()
    service = discovery.build('language', 'v1', credentials=credentials)
    service_request = service.documents().analyzeSentiment(
        body={
            'document': {
                'type': 'PLAIN_TEXT',
                'language': 'ja-JP',
                'content': msg
            },
            'encodingType': 'UTF8'
        }
    )
    response = service_request.execute()
    return response['documentSentiment']['score'], response['documentSentiment']['magnitude']

def parse_drinking_amount(msg, amount=None):
    msg = msg.translate({
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
        msg = unicodedata.normalize('NFKC', msg)
    soup = call_yahoo_jparser(msg, 'MA')
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

def parse_message(msg):
    if type(msg) == unicode:
        msg = unicodedata.normalize('NFKC', msg)
    soup = call_yahoo_jparser(msg, 'DA')

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
    history_id = -1
    # I'm not sure that I have to use Yahoo API...
    # I just wanted to use that !!!
    for id, elm in elms.items():
        for morphem in elm['morphemlist']:
            if morphem['pos'] == u'動詞' or morphem['pos'] == u'名詞' or morphem['pos'] == u'副詞':
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
                elif (morphem['surface'].find(u'過去') >= 0 or
                      morphem['surface'].find(u'前') >= 0):
                    history_id = id
                    # do not break with history morphem
                else:
                    mo = re.search(u'(今|これ)まで', morphem['surface'])
                    if mo:
                        history_id = id
                        # do not break with history morphem
        else:
            continue
        break

    return (elms, drink_id, cancel_id, finished_id, history_id)

def handle_message(user_id, msg):
    mid = user_id
    (elms, drink_id, cancel_id, finished_id, history_id) = parse_message(msg)

    if history_id >= 0 and drink_id >= 0 and depends_drink(history_id, elms, drink_id):
        # this is request for drinking history
        return history_drinking(mid)

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
    now = utc_now().replace(second=0, microsecond=0)
    s_date = datetime.now(tz=tz_jst).replace(second=0, microsecond=0)
    for id, elm in elms.items():
        if depends_drink(id, elms, drink_id):
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

    # store data
    watches = []
    utc_s_date = s_date.astimezone(tz_utc).replace(tzinfo=None)
    s_date_str = u'%d月%d日%d時%d分' % (s_date.month, s_date.day, s_date.hour, s_date.minute)

    # check if s_date is valid
    if utc_s_date < now:
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

    msg = s_date_str + u'から飲むのですね！\n約%d分毎に%d回メッセージを送信しますので、何を何杯飲んだかなど、状況を返信してくださいね。帰宅したら帰宅とメッセージしてください。' % (WATCH_INTERVAL, WATCH_COUNTS)

    # past drinkings
    query = Drinking.query(Drinking.mid==mid, Drinking.is_done==True).order(-Drinking.start_date)
    prev_drinkings = query.fetch(1)
    if len(prev_drinkings):
        prev_drinking = prev_drinkings[0]
        msg += u'\n\nちなみに前回の飲みは%sで、その時は%s\n' % (format_jdate(prev_drinking.start_date.
                                                                        replace(tzinfo=tz_utc).astimezone(tz_jst)), get_drinking_quality_word(prev_drinking.sentiment, prev_drinking.magnitude))
        sep = u''
        for kind in prev_drinking.summary:
            msg += sep + u'   %s %d 杯' % (kind, prev_drinking.summary[kind])
            sep = u'\n'

    return msg

def handle_result(text, info):
    drinking = Drinking.get_key(info['key']).get()
    if drinking:
        (sentiment, magnitude) = call_google_sentiment_analytics(text)
        drinking.result = text
        drinking.sentiment = sentiment
        drinking.magnitude = magnitude
        drinking.put()
        msg = u'昨日は' + get_drinking_quality_word(sentiment, magnitude) + \
              u'\n次回も大人飲みのお手伝いをします。またメッセージくださいね！'
        return msg
    else:
        return None

def handle_reply(text, watch_info):
    drinking = Drinking.get_key(watch_info['key']).get()
    (elms, drink_id, cancel_id, finished_id, history_id) = parse_message(text)
    if drinking:
        summary = parse_drinking_amount(text, drinking.summary)
        drinking.watches[watch_info['idx']].reply = text
        drinking.watches[watch_info['idx']].is_replied = True
        drinking.summary = summary
        if len(summary):
            msg = u'これまで合計\n'
            for kind in summary:
                msg += u'%s %d杯\n' % (kind, summary[kind])
            msg += u'飲みました！\n'
        else:
            msg = u''
        
        if finished_id >= 0:
            # it is finished
            msg += finish_the_drinkig(drinking)
        else:
            msg += u'引き続き大人飲みでいきましょう！'
            drinking.put()
        return msg
    else:
        return None

def reply_message(reply_token, text):
    if is_on_local_server():
        # I'm not running on server
        return

    line_bot_api = LineBotAPI(APP_KEYS['line']['token'])
    line_bot_api.reply_message(text, reply_token)


def send_message(mids, text):
    if 'SERVER_SOFTWARE' not in os.environ or \
       os.environ['SERVER_SOFTWARE'].find('testbed') >= 0:
        # I'm not running on server
        return

    if len(mids) == 0:
        return

    line_bot_api = LineBotAPI(APP_KEYS['line']['token'])
    for mid in mids:
        user_id = re.sub(r'^u', 'U', mid)
        line_bot_api.send_message(text, user_id)

