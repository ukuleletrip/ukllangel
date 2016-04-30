#! /usr/bin/env python
# -*- coding:utf-8 -*-

"""Callback Handler from LINE Bot platform"""

__author__ = 'ukuleletrip@gmail.com (Ukulele Trip)'

import webapp2
from google.appengine.api import urlfetch
import urllib
import json
import logging
from appkeys import APP_KEYS

class BotCallbackHandler(webapp2.RequestHandler):
    def post(self):
        #params = json.loads(self.request.body.decode('utf-8'))
        params = json.loads(self.request.body)
        logging.debug('kick from line server,\n %s' % (params['result']))
        SendMessage(params['result'][0]['content']['from'],
                    params['result'][0]['content']['text'])
        self.response.write(json.dumps({}))


def SendMessage(to, text):
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
    logging.debug(params)
    #data = urllib.urlencode(params)
    data = params
    result = urlfetch.fetch(
        url=url,
        payload=json.dumps(data,ensure_ascii=False),
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
        logging.debug(result.content)    
