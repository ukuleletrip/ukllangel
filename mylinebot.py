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
import logging
from appkeys import APP_KEYS
import re
from datetime import datetime, timedelta
from db import User, Watch, Drinking
import os
import uuid
import jinja2
from linebotapi import LineBotAPI, WebhookRequest
from googleapiclient import discovery
from oauth2client.client import GoogleCredentials
from gaeutil import *
from message import Message


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

service_url = 'https://ukllangel.appspot.com'

usage = u'大人飲みのお手伝いをします！使い方は %s/help を参照してください。' % (service_url)
welcome = usage

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

    # check it is still valid
    if users[0].history_expire < utc_now():
        # expired
        return None

    drinkings = get_drinking_history(users[0].key.id())
    t_drinkings = []
    for drinking in drinkings:
        t_drinking = {}
        t_drinking['started'] = format_jdate(utc_to_jst(drinking.start_date))
        t_drinking['finished'] = utc_to_jst(drinking.finished_date).strftime('%H:%M') \
                                 if drinking.finished_date else u''
        t_drinking['result'] = drinking.result if drinking.result else u''
        t_drinking['watches'] = []
        for watch in drinking.watches:
            if watch.sent_count > 0:
                t_watch = {}
                t_watch['date'] = utc_to_jst(watch.date).strftime('%H:%M')
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
    user.history_expire = utc_now()+timedelta(minutes=HISTORY_DURATION)
    user.put()

    msg = ''
    if worst_drinking:
        msg += u'最悪の飲みは %s だったようです。\n' % \
               (format_jdate(utc_to_jst(worst_drinking.start_date)))
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

def handle_message_event(recv_req):
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

def handle_follow_event(recv_req):
    # add as friend
    reply_message(recv_req.get_reply_token(), welcome)

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


def handle_message(user_id, msg):
    mid = user_id
    message = Message(msg)

    if message.type == Message.REQUEST_HISTORY:
        return history_drinking(mid)
    elif message.type == Message.FINISH_DRINKING:
        return finish_drinking(mid)
    elif message.type == Message.CANCEL_DRINKING:
        return cancel_drinking(mid)
    elif message.type == Message.NO_MEANING:
        return None
    
    # user can have only one drink
    if has_drinking(mid):
        return u'飲みは1つしか予約できません。予約した飲みをキャンセルするには「やめ」とメッセージしてください。'

    s_date = message.param

    # store data
    watches = []
    utc_s_date = jst_to_utc(message.param).replace(tzinfo=None)
    s_date_str = u'%d月%d日%d時%d分' % (s_date.month, s_date.day, s_date.hour, s_date.minute)

    # check if s_date is valid
    now = just_minute(utc_now())
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
        msg += u'\n\nちなみに前回の飲みは%sで、その時は%s\n' % \
               (format_jdate(utc_to_jst(prev_drinking.start_date)),
                get_drinking_quality_word(prev_drinking.sentiment, prev_drinking.magnitude))
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
    message = Message(text)
    if drinking:
        summary = message.parse_drinking_amount(drinking.summary)
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
        
        if message.type == Message.FINISH_DRINKING:
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

