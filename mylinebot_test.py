#! /usr/bin/env python
# -*- coding:utf-8 -*-

import sys, os
sys.path.insert(1, '/usr/local/google_appengine')
sys.path.insert(1, '/usr/local/google_appengine/lib/yaml/lib')

import unittest
from google.appengine.api import memcache
from google.appengine.ext import ndb
from google.appengine.ext import testbed

from mylinebot import *
from db import Drinking, Watch
from datetime import datetime, timedelta
from tzimpl import JST, UTC
from time import sleep

tz_jst = JST()
tz_utc = UTC()

class MyLineBotTestCase(unittest.TestCase):
    def setUp(self):
        # First, create an instance of the Testbed class.
        self.testbed = testbed.Testbed()
        # Then activate the testbed, which prepares the service stubs for use.
        self.testbed.activate()
        # Next, declare which service stubs you want to use.
        self.testbed.init_urlfetch_stub()
        self.testbed.init_datastore_v3_stub()
        self.testbed.init_memcache_stub()
        # Clear ndb's in-context cache between tests.
        # This prevents data from leaking between tests.
        # Alternatively, you could disable caching by
        # using ndb.get_context().set_cache_policy(False)
        ndb.get_context().clear_cache()


    def tearDown(self):
        self.testbed.deactivate()

        
    def testParseDate(self):
        test_patterns = []
        now = datetime.now()+timedelta(minutes=5)

        test_patterns.append({ 'date'  : now,
                               'msg'   : u'%d時%d分から飲む' % (now.hour, now.minute),
                               'msgpm' : u'午後%d時%d分から呑む' % (now.hour%12, now.minute)})
        test_patterns.append({ 'date'  : now,
                               'msg'   : u'%02d時%02d分からのむ' % (now.hour, now.minute),
                               'msgpm' : u'PM%02d時%02d分から飲む' % (now.hour%12, now.minute)})
        test_patterns.append({ 'date'  : now,
                               'msg'   : u'%02d%02dから呑む' % (now.hour, now.minute),
                               'msgpm' : u'%02d%02dPMからのむ' % (now.hour%12, now.minute)})
        now2 = (now+timedelta(hours=1)).replace(minute=0)
        test_patterns.append({ 'date'  : now2,
                               'msg'   : u'%d時から飲む' % (now2.hour),
                               'msgpm' : u'pm%d時から飲む' % (now2.hour%12)})

        for test in test_patterns:
            dt = test['date']

            msg = parse_message('test', test['msg'])
            c_msg = u'%d月%d日%d時%d分から飲むのですね' % (dt.month, dt.day, dt.hour, dt.minute)
            self.assertTrue(msg.startswith(c_msg), msg + '\n' + c_msg)
            cancel_drinking('test')

            msg = parse_message('test', test['msgpm'])
            c_msg = u'%d月%d日%d時%d分から飲むのですね' % \
                    (dt.month, dt.day, dt.hour if dt.hour >=12 else dt.hour+12, dt.minute)
            self.assertTrue(msg.startswith(c_msg), msg + '\n' + c_msg)
            cancel_drinking('test')
                

    def testPastDrinking(self):
        now = datetime.now()+timedelta(minutes=-5)
        msg = u'%02d%02dから飲む' % (now.hour, now.minute)
        msg = parse_message('test', msg)
        self.assertTrue(msg.find(u'は過去です')>=0, msg)


    def testDuplicatedDrinking(self):
        now = datetime.now()+timedelta(minutes=5)
        msg = u'%02d%02dから飲む' % (now.hour, now.minute)
        parse_message('test', msg)
        msg = parse_message('test', msg)
        self.assertTrue(msg.find(u'飲みは1つしか予約できません')>=0, msg)


    def testWatch(self):
        TEST_INTERVAL = 10
        test_id = 'test'
        watches = []
        now_utc = (datetime.now(tz=tz_utc)+timedelta(seconds=TEST_INTERVAL)).replace(tzinfo=None)
        for i in range(WATCH_COUNTS):
            watches.append(Watch(date=now_utc+timedelta(seconds=TEST_INTERVAL*(i+1))))

        drinking = Drinking(id=test_id,
                            mid=test_id,
                            start_date=now_utc,
                            watches=watches)
        drinking.put()

        sleep(TEST_INTERVAL+2)

        for i in range(WATCH_COUNTS):
            sleep(TEST_INTERVAL)

            watch_drinkings()
            (status, info) = get_status(test_id, True)
            self.assertEqual(status, STAT_WAIT_REPLY)
            self.assertTrue(info is not None)
            self.assertEqual(info['key'], test_id, info['key'])
            self.assertEqual(info['idx'], i, info['idx'])
            drinking = Drinking.get_key(test_id).get()
            for j, watch in enumerate(drinking.watches):
                self.assertEqual(watch.is_replied, True if j < i else False)
                self.assertEqual(watch.sent_count, 1 if j <= i else 0)

            self.assertFalse(drinking.is_done)
            content = { 'from' : test_id, 'text' : 'OK' }
            receive_message(content)
            (status, info) = get_status(test_id)
            self.assertEqual(status, STAT_NONE)
            drinking = Drinking.get_key(test_id).get()
            for j, watch in enumerate(drinking.watches):
                self.assertEqual(watch.is_replied, True if j <= i else False)
                self.assertEqual(watch.sent_count, 1 if j <= i else 0)

        drinking = Drinking.get_key(test_id).get()
        self.assertTrue(drinking.is_done)


    def testWatchDelayed(self):
        TEST_INTERVAL = 5
        test_id = 'test'
        watches = []
        now_utc = (datetime.now(tz=tz_utc)+timedelta(seconds=TEST_INTERVAL)).replace(tzinfo=None)
        for i in range(WATCH_COUNTS):
            watches.append(Watch(date=now_utc+timedelta(seconds=TEST_INTERVAL*(i+1))))

        drinking = Drinking(id=test_id,
                            mid=test_id,
                            start_date=now_utc,
                            watches=watches)
        drinking.put()

        sleep(TEST_INTERVAL*2+2)

        for i in range(5):
            watch_drinkings()
            (status, info) = get_status(test_id, True)
            self.assertEqual(status, STAT_WAIT_REPLY)
            self.assertTrue(info is not None)
            self.assertEqual(info['key'], test_id, info['key'])
            self.assertEqual(info['idx'], 0)
            drinking = Drinking.get_key(test_id).get()
            for j, watch in enumerate(drinking.watches):
                self.assertEqual(watch.is_replied, False)
                self.assertEqual(watch.sent_count, i+1 if j == 0 else 0)

        drinking = Drinking.get_key(test_id).get()
        self.assertFalse(drinking.is_done)


    def testWatchFinished(self):
        TEST_INTERVAL = 5
        test_id = 'test'
        watches = []
        now_utc = (datetime.now(tz=tz_utc)+timedelta(seconds=TEST_INTERVAL)).replace(tzinfo=None)
        for i in range(WATCH_COUNTS):
            watches.append(Watch(date=now_utc+timedelta(seconds=TEST_INTERVAL*(i+1))))

        drinking = Drinking(id=test_id,
                            mid=test_id,
                            start_date=now_utc,
                            watches=watches)
        drinking.put()

        sleep(TEST_INTERVAL*2+2)

        watch_drinkings()
        msg = parse_reply(test_id, u'帰宅した', memcache.get(test_id))
        self.assertTrue(msg.startswith(u'お疲れさまでした'), msg)

        drinking = Drinking.get_key(test_id).get()
        for j, watch in enumerate(drinking.watches):
            self.assertEqual(watch.is_replied, True if j == 0 else False)
            self.assertEqual(watch.sent_count, 1 if j == 0 else 0)
        self.assertTrue(drinking.is_done)


    def testResult(self):
        test_id = 'test'
        watches = []
        now_utc = (datetime.now(tz=tz_utc)+timedelta(days=-1)).replace(tzinfo=None)
        for i in range(WATCH_COUNTS):
            watches.append(Watch(date=now_utc+timedelta(seconds=WATCH_INTERVAL*(i+1)),
                                 sent_count=1,
                                 is_replied=True))

        drinking = Drinking(id=test_id,
                            mid=test_id,
                            start_date=now_utc,
                            is_done=True,
                            watches=watches)
        drinking.put()

        check_result()
        (status, info) = get_status(test_id)
        self.assertEqual(status, STAT_WAIT_RESULT)

        result = u'二日酔い'
        msg = parse_result(test_id, result, info)

        drinking = Drinking.get_key(test_id).get()
        self.assertEqual(drinking.result, result, drinking.result)
        self.assertTrue(msg.find(u'次回も大人飲み') >= 0, msg)


if __name__ == '__main__':
    unittest.main()
