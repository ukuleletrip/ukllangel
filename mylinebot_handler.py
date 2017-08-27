#! /usr/bin/env python
# -*- coding:utf-8 -*-
#
# you have to install Beautifulsoup.
# $ mkdir libs
# $ pip install -t libs beautifulsoup4


"""Callback Handler from LINE Bot platform"""

__author__ = 'ukuleletrip@gmail.com (Ukulele Trip)'

import webapp2
import json
import logging
from appkeys import APP_KEYS
from datetime import datetime, timedelta
import hmac, hashlib, base64
from linebotapi import LineBotAPI, WebhookRequest, is_valid_signature

from mylinebot import handle_message_event, handle_follow_event, watch_drinkings, check_result, get_drinking_history_content

usage = u'「xx時xx分から飲む」などとメッセージするとその時間の1、2、3時間後に飲み過ぎていないか確認するメッセージを送信します。\n途中で止めたい時、無事帰宅した時は「帰宅」や「やめ」とメッセージしてください。'
welcome = u'ようこそ！大人飲みのためのLINE Botサービスです！\n?をメッセージすると使い方を返信します。'

class BotCallbackHandler(webapp2.RequestHandler):
    """Callback handler class which is called by LINE server.

    It handles all messages created by users on LINE talk.
    """
    def post(self):
        #params = json.loads(self.request.body.decode('utf-8'))
        recv_req = WebhookRequest(self.request.body)
        line_bot_api = LineBotAPI(APP_KEYS['line']['token'])

        logging.debug('kick from line server,\n %s' % (self.request.body))

        if is_valid_signature(APP_KEYS['line']['secret'],
                              self.request.headers.get('X-LINE-Signature'),
                              self.request.body):
            if recv_req.is_text_message():
                handle_message_event(recv_req)
            elif recv_req.is_follow_event():
                handle_follow_event(recv_req)

        self.response.write(json.dumps({}))


class WatchingHandler(webapp2.RequestHandler):
    """Handler which is called by GAE cron, every x minutes.

    It is used for watching drinking status.
    """
    def get(self):
        watch_drinkings()


class ReqResultHandler(webapp2.RequestHandler):
    """Handler which is called by GAE cron at xx:xx every day.

    It is used for requesting user to tell their status after drinkings.
    """
    def get(self):
        check_result()


class HistoryHandler(webapp2.RequestHandler):
    """Handler for drinking history URL.

    It creates contents for drinking history.
    """
    def get(self):
        elms = self.request.path.split('/')
        content = get_drinking_history_content(elms[-1])
        if content is None:
            self.abort(404)
        self.response.write(content)

