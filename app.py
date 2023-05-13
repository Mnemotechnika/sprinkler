#!/usr/bin/python3
# -*- coding: utf-8 -*-

console_title = "\
+----------------------------------+\n\
|  Dadpuszta AppServer             |\n\
|  m.laszlo1985@gmail.com          |\n\
|  2019.07.19                      |\n\
+----------------------------------+\n"

import os
import sys
import logging
import pickle
import json
import datetime
import time
import re

import tornado.httpserver
import tornado.ioloop
import tornado.web
from tornado.websocket import WebSocketHandler
from tornado.options import define, options

logger = logging.getLogger('app_server')
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

import RPi.GPIO as GPIO

class BaseHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return self.get_secure_cookie("user")
        
class LoginHandler(BaseHandler):
    def get(self, *args):
        self.render('login.html')

    def post(self, *args):
        username = self.get_argument("name", "")
        password = self.get_argument("pass", "")
        if (username == "dadpuszta" and password == "nakonxipan") or (username == "sprinkler" and password == "sprinkler"):
            self.set_secure_cookie("user", username)
        self.redirect("/")

class LogoutHandler(BaseHandler):
    def get(self, *args):
        self.set_secure_cookie("user",'');
        self.redirect("/")

class IndexHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, *args):
        #name = tornado.escape.xhtml_escape(self.current_user)
        self.render('index.html')

class SettingsHandler(BaseHandler):

    def chk_time(self, sr_time):
        s_time = "00:00"
        sr = re.search(r"\b[0-9]{2}:[0-9]{2}" ,sr_time)
        if sr is not None:
            s_time = sr.string
        return s_time

    @tornado.web.authenticated
    def get(self, *args):
        config = self.application.settings.get( 'sprinkler_config' )
        
        kwd = dict(
            work_en = config['work_en'],
            work_start = config['work_start'],
            work_end = config['work_end'],
        )
        self.render('settings.html', **kwd)
        
    @tornado.web.authenticated
    def post(self, *args):
        config = self.application.settings.get( 'sprinkler_config' )
        
        work_en = self.get_argument("work_en", "0")
        work_start = self.get_argument("work_start", "00:00")
        work_end = self.get_argument("work_end", "00:00")
        
        config['work_en'] = "0" if work_en == "0" else "1"
        config['work_start'] = self.chk_time(work_start)
        config['work_end'] = self.chk_time(work_end)
        
        print(config)
        
        self.application.config_save()
        
        self.redirect('/settings')

class WebSocketHandler( WebSocketHandler ):
    def open( self, *args ):
        
        clients = self.application.settings.get( 'socket_clients' )
        self.set_nodelay( True )
        
        self.client_id = None
        
        try:
            self.client_id = self.get_argument("id")
            print ('[WebsocketHandler] Id: %s' % self.client_id)
        
        except:
            self.close()
            print ('[WebsocketHandler] Id missing, closing connection')
            
        finally:
            if self.client_id is not None:
                
                if self.client_id == 'control':
                    client_count = 0
                    while True:
                        check_id = self.client_id + '_' + str(client_count)
                        if check_id not in clients.keys():
                            self.client_id = check_id
                            break;
                        client_count += 1
                
                clients[self.client_id] = {"id": self.client_id, "object": self}
                    
                print ('[WebsocketHandler] Client %s connected' % self.client_id)
    
    
    def on_message( self, message ):
        clients = self.application.settings.get( 'socket_clients' )
        
        try:
            json_message = json.loads(message)
            if (json_message['type'] == "system"):
                if (json_message['data'] == "system_shutdown"):
                    self.application.os_shutdown()
                if (json_message['data'] == "system_reboot"):
                    self.application.os_reboot()
                    
            elif (json_message['type'] == "sprinkler"):
                command = json_message['data']
                if (command['cmd'] == "set"):
                    self.application.relay_started = True
                    if (command['value'] == "1"):
                        self.application.gpio_set(int(command['channel']),GPIO.LOW)
                    else:
                        self.application.gpio_set(int(command['channel']))
                
                elif (command['cmd'] == "setall"):
                    self.application.relay_started = True
                    if (command['value'] == "1"):
                        self.application.gpio_setall(GPIO.LOW)
                    else:
                        self.application.gpio_setall()
                
                elif (command['cmd'] == "start"):
                    self.application.playlist_start()
                elif (command['cmd'] == "stop"):
                    self.application.playlist_stop()
                    
            elif (json_message['type'] == "status"):
                self.application.gpio_report()
            else:
                for cid in clients:
                    if self.client_id != clients[cid]["object"].client_id:
                        #logger.debug("Sending to %s"%clients[cid]["object"].client_id)
                        clients[cid]["object"].write_message(message)        
        except:
            print ('[WebsocketHandler] JSON parse failed')
            
    def on_close( self ):
        clients = self.application.settings.get( 'socket_clients' )
        if self.client_id in clients.keys():
            del clients[ self.client_id ]
            print( '[Socket] Client %s disconnected' % self.client_id )


define("port", default=8888, help="run on the given port", type=int)
define("debug", default=True, help="debug mode")
tornado.options.parse_command_line()

class Application( tornado.web.Application ):
    
    relay_pins = [17,18,27,22,23,24,25,5,6,12,13,16]
    relay_status = [0,0,0,0,0,0,0,0,0,0,0,0]
    relay_block = [0,0,0,0,0,0,0,1,0,0,0,0]
    relay_enabled = True
    relay_enabled_last = True
    
    playlist = [
        [1,1,1,1,0,0,0,0,0,0,0,0],
        [0,1,1,1,1,0,0,0,0,0,0,0],
        [0,0,1,1,1,1,0,0,0,0,0,0],
        [0,0,0,1,1,1,1,0,0,0,0,0],
        [0,0,0,0,1,1,1,1,0,0,0,0],
        [0,0,0,0,0,1,1,1,1,0,0,0],
        [0,0,0,0,0,0,1,1,1,1,0,0],
        [0,0,0,0,0,0,0,1,1,1,1,0],
        [0,0,0,0,0,0,0,0,1,1,1,1],
        [1,0,0,0,0,0,0,0,0,1,1,1],
        [1,1,0,0,0,0,0,0,0,0,1,1],
        [1,1,1,0,0,0,0,0,0,0,0,1],

        [1,1,0,0,0,0,0,0,0,0,0,0],
        [0,1,1,0,0,0,0,0,0,0,0,0],
        [0,0,1,1,0,0,0,0,0,0,0,0],
        [0,0,0,1,1,0,0,0,0,0,0,0],
        [0,0,0,0,1,1,0,0,0,0,0,0],
        [0,0,0,0,0,1,1,0,0,0,0,0],
        [0,0,0,0,0,0,1,1,0,0,0,0],
        [0,0,0,0,0,0,0,1,1,0,0,0],
        [0,0,0,0,0,0,0,0,1,1,0,0],
        [0,0,0,0,0,0,0,0,0,1,1,0],
        [0,0,0,0,0,0,0,0,0,0,1,1],
        [1,0,0,0,0,0,0,0,0,0,0,1],

        [1,0,0,0,0,0,1,0,0,0,0,0],
        [0,1,0,0,0,0,0,1,0,0,0,0],
        [0,0,1,0,0,0,0,0,1,0,0,0],
        [0,0,0,1,0,0,0,0,0,1,0,0],
        [0,0,0,0,1,0,0,0,0,0,1,0],
        [0,0,0,0,0,1,0,0,0,0,0,1],

        [0,0,1,1,0,0,0,0,0,0,0,0],
        [0,1,0,0,1,0,0,0,0,0,0,0],
        [1,0,0,0,0,1,0,0,0,0,0,0],
        [0,0,0,0,0,0,1,0,0,0,0,1],
        [0,0,0,0,0,0,0,1,0,0,1,0],
        [0,0,0,0,0,0,0,0,1,1,0,0],
        [0,0,0,0,0,0,0,1,0,0,1,0],
        [0,0,0,0,0,0,1,0,0,0,0,1],
        [1,0,0,0,0,1,0,0,0,0,0,0],
        [0,1,0,0,1,0,0,0,0,0,0,0],
        [0,0,1,1,0,0,0,0,0,0,0,0],

        [1,0,0,1,0,0,1,0,0,1,0,0],
        [0,1,0,0,1,0,0,1,0,0,1,0],
        [0,0,1,0,0,1,0,0,1,0,0,1],
        [0,1,0,0,1,0,0,1,0,0,1,0],
    ]
    
    playlist_enabled = False
    playlist_current_step = 0
    playlist_step_count = len(playlist)

    current_ms = 0.0
    last_ms = 0.0
    start_ms = 0.0
    interval_ms = 5.0
    limit_ms = 1200.0
    
    app_data_path = os.path.dirname(os.path.abspath(__file__))
    
    def __init__( self ):
        
        self.sprinkler_config_file = os.path.join(self.app_data_path, "sprinkler.conf" )
        
        app_settings = dict(
            template_path = os.path.join(self.app_data_path , "template" ),
            static_path = os.path.join(self.app_data_path, "static" ),
            socket_clients = dict(),
            sprinkler_config = {"work_en":"0","work_start":"07:00","work_end":"20:00"},
            debug = options.debug,
            login_url = "/login",
            cookie_secret = "__TODO:_GENERATE_YOUR_OWN_RANDOM_VALUE_HERE__",
            xsrf_cookies = True,
        )
        
        url_patterns = [
            ( r'/login', LoginHandler),
            ( r'/logout', LogoutHandler),
            ( r'/settings', SettingsHandler),
            ( r'/ws', WebSocketHandler),
            ( r'/(favicon.ico)', tornado.web.StaticFileHandler, {'path': '/static/favicon.ico'} ),
            ( r'/(.*)', IndexHandler ),
        ]
        
        #self.config_load()
        self.gpio_init()
        
        super( Application, self ).__init__( url_patterns, **app_settings )
    
    def gpio_init( self ):
        GPIO.setwarnings( False )
        GPIO.setmode( GPIO.BCM )
        GPIO.setup( self.relay_pins, GPIO.OUT, initial=GPIO.HIGH)

    def gpio_cleanup( self ):
        GPIO.cleanup( self.relay_pins )
        
    def gpio_set( self, channel, value=GPIO.HIGH ):
        if self.relay_block[channel] == 1:
            GPIO.output( self.relay_pins[channel], GPIO.HIGH )
            self.relay_status[channel] = 0
        else:
            GPIO.output( self.relay_pins[channel], value )
            if (value == GPIO.LOW):
                self.relay_status[channel] = 1
            else:
                self.relay_status[channel] = 0
        self.gpio_report()

    def gpio_setall( self, value=GPIO.HIGH ):
        for channel in range(12):
            if self.relay_block[channel] == 1:
                GPIO.output( self.relay_pins[channel], GPIO.HIGH )
                self.relay_status[channel] = 0
            else:
                GPIO.output( self.relay_pins[channel], value )
                if (value == GPIO.LOW):
                    self.relay_status[channel] = 1
                else:
                    self.relay_status[channel] = 0
        self.gpio_report()
    
    def gpio_play_set(self, values):
        for channel in range(12):
            if self.relay_block[channel] == 1:
                GPIO.output( self.relay_pins[channel], GPIO.HIGH )
                self.relay_status[channel] = 0
            else:
                GPIO.output( self.relay_pins[channel], values[channel] )
                self.relay_status[channel] = values[channel]
                
        #GPIO.output( self.relay_pins, values )
        #self.relay_status = values
        self.gpio_report()
        
    def gpio_report(self):
        json_message = json.dumps({'type':'status','data':{"relay":self.relay_status,"enable":self.relay_enabled,"play":self.playlist_enabled}})
        clients = self.settings.get( 'socket_clients' )
        for cid in clients:
            clients[cid]["object"].write_message(json_message) 
            
    def config_load( self ):
        config = self.settings.get( 'sprinkler_config' )
        try:
            with open(self.sprinkler_config_file, 'rb') as handle:
                config = pickle.load( handle )
        except Exception as e:
            logger.info("config load failed %s"%(str(e)))
    
    def config_save( self ):
        config = self.settings.get( 'sprinkler_config' )
        try:
            with open(self.sprinkler_config_file, 'wb') as handle:
                pickle.dump( config, handle, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as e:
            logger.info("config load failed %s"%(str(e)))
    
    def os_shutdown( self ):
        os.system( "sudo shutdown -h now" )
    
    def os_reboot( self ):
        os.system( "sudo shutdown -r now" )
    
    def playlist_start(self):
        self.playlist_enabled = True
        self.playlist_current_step = 0
        self.start_ms = time.time()
        self.gpio_report()
    
    def playlist_stop(self):
        self.playlist_enabled = False
        self.gpio_report()
    
    def sprinkler_loop( self ):
        sprinkler_config = self.settings.get('sprinkler_config')
        
        if (sprinkler_config['work_en'] == "1"):
            s_start = datetime.datetime.strptime(sprinkler_config['work_start'],'%H:%M')
            s_end = datetime.datetime.strptime(sprinkler_config['work_end'],'%H:%M')
            s_now = datetime.datetime.now()
            d1 = (s_start.hour, s_start.minute)
            d2 = (s_now.hour, s_now.minute)
            d3 = (s_end.hour, s_end.minute)
            if d1 < d2 < d3:
                self.relay_enabled = True
            else:
                self.relay_enabled = False
                
            if (self.relay_enabled != self.relay_enabled_last):
                self.relay_enabled_last = self.relay_enabled
                
                if not self.relay_enabled:
                    self.gpio_setall()
                    logger.info("disable")
                else:
                    logger.info("enable")
        else:
            self.relay_enabled = True
        
        if self.relay_enabled and self.playlist_enabled:
            
            self.current_ms = time.time()
            
            if (self.last_ms + self.interval_ms) < self.current_ms:
                self.last_ms = self.current_ms
                
                current_value = self.playlist[self.playlist_current_step]
                self.gpio_play_set(current_value)
                
                self.playlist_current_step = self.playlist_current_step + 1
                if self.playlist_current_step > self.playlist_step_count - 1:
                    self.playlist_current_step = 0
                
                #print('tick',current_value)
            
            if (self.start_ms + self.limit_ms) < self.current_ms:
                self.playlist_stop()
                    
        
            
            
if __name__ == "__main__":
    
    app = Application()
    app.config_load()
    try:
        print( console_title )
        http_server = tornado.httpserver.HTTPServer(app)
        http_server.listen(options.port)
        sprinkler_call = tornado.ioloop.PeriodicCallback(app.sprinkler_loop, 100)
        sprinkler_call.start()
        logger.info('started on port %s' % (options.port))
        tornado.ioloop.IOLoop.instance().start()

    except (KeyboardInterrupt, SystemExit) as e:
        app.gpio_cleanup()
        logger.info('stopped')
        sys.exit(0)
