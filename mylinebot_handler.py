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

from mylinebot import receive_message, receive_operation, watch_drinkings, check_result

usage = u'「xx時xx分から飲む」などとメッセージするとその時間の1、2、3時間後に飲み過ぎていないか確認するメッセージを送信します。\n途中で止めたい時、無事帰宅した時は「帰宅」や「やめ」とメッセージしてください。'
welcome = u'ようこそ！大人飲みのためのLINE Botサービスです！\n?をメッセージすると使い方を返信します。'

def is_valid_signature(request):
    signature = base64.b64encode(hmac.new(APP_KEYS['line']['secret'],
                                          request.body,
                                          hashlib.sha256).digest())
    return signature == request.headers.get('X-LINE-ChannelSignature')

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
        watch_drinkings()


class ReqResultHandler(webapp2.RequestHandler):
    def get(self):
        check_result()


