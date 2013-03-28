"""Tests for http/wsgi.py"""

import io
import unittest
import unittest.mock

import tulip
from tulip.http import wsgi
from tulip.http import protocol
from tulip.test_utils import LogTrackingTestCase


class HttpWsgiServerProtocolTests(LogTrackingTestCase):

    def setUp(self):
        super().setUp()
        self.suppress_log_errors()

        self.loop = tulip.new_event_loop()
        tulip.set_event_loop(self.loop)

        self.wsgi = unittest.mock.Mock()
        self.stream = unittest.mock.Mock()
        self.transport = unittest.mock.Mock()
        self.transport.get_extra_info.return_value = '127.0.0.1'

        self.payload = b'data'
        self.info = protocol.RequestLine('GET', '/path', (1, 0))
        self.headers = []
        self.message = protocol.RawHttpMessage(
            self.headers, b'data', True, 'deflate')

    def tearDown(self):
        self.loop.close()
        super().tearDown()

    def test_ctor(self):
        srv = wsgi.WSGIServerHttpProtocol(self.wsgi)
        self.assertIs(srv.wsgi, self.wsgi)
        self.assertFalse(srv.readpayload)

    def _make_one(self, **kw):
        srv = wsgi.WSGIServerHttpProtocol(self.wsgi, **kw)
        srv.stream = self.stream
        srv.transport = self.transport
        return srv.create_wsgi_environ(self.info, self.message, self.payload)

    def test_environ(self):
        environ = self._make_one()
        self.assertEqual(environ['RAW_URI'], '/path')
        self.assertEqual(environ['wsgi.async'], True)

    def test_environ_except_header(self):
        self.headers.append(('EXPECT', '101-continue'))
        self._make_one()
        self.assertFalse(self.transport.write.called)

        self.headers[0] = ('EXPECT', '100-continue')
        self._make_one()
        self.transport.write.assert_called_with(
            b'HTTP/1.1 100 Continue\r\n\r\n')

    def test_environ_headers(self):
        self.headers.extend(
            (('HOST', 'python.org'),
             ('SCRIPT_NAME', 'script'),
             ('CONTENT-TYPE', 'text/plain'),
             ('CONTENT-LENGTH', '209'),
             ('X_TEST', '123'),
             ('X_TEST', '456')))
        environ = self._make_one(is_ssl=True)
        self.assertEqual(environ['CONTENT_TYPE'], 'text/plain')
        self.assertEqual(environ['CONTENT_LENGTH'], '209')
        self.assertEqual(environ['HTTP_X_TEST'], '123,456')
        self.assertEqual(environ['SCRIPT_NAME'], 'script')
        self.assertEqual(environ['SERVER_NAME'], 'python.org')
        self.assertEqual(environ['SERVER_PORT'], '443')

    def test_environ_host_header(self):
        self.headers.append(('HOST', 'python.org'))
        environ = self._make_one()

        self.assertEqual(environ['HTTP_HOST'], 'python.org')
        self.assertEqual(environ['SERVER_NAME'], 'python.org')
        self.assertEqual(environ['SERVER_PORT'], '80')
        self.assertEqual(environ['SERVER_PROTOCOL'], 'HTTP/1.0')

    def test_environ_host_port_header(self):
        self.info = protocol.RequestLine('GET', '/path', (1, 1))
        self.headers.append(('HOST', 'python.org:443'))
        environ = self._make_one()

        self.assertEqual(environ['HTTP_HOST'], 'python.org:443')
        self.assertEqual(environ['SERVER_NAME'], 'python.org')
        self.assertEqual(environ['SERVER_PORT'], '443')
        self.assertEqual(environ['SERVER_PROTOCOL'], 'HTTP/1.1')

    def test_environ_forward(self):
        self.transport.get_extra_info.return_value = 'localhost,127.0.0.1'
        environ = self._make_one()

        self.assertEqual(environ['REMOTE_ADDR'], '127.0.0.1')
        self.assertEqual(environ['REMOTE_PORT'], '80')

        self.transport.get_extra_info.return_value = 'localhost,127.0.0.1:443'
        environ = self._make_one()

        self.assertEqual(environ['REMOTE_ADDR'], '127.0.0.1')
        self.assertEqual(environ['REMOTE_PORT'], '443')

        self.transport.get_extra_info.return_value = ('127.0.0.1', 443)
        environ = self._make_one()

        self.assertEqual(environ['REMOTE_ADDR'], '127.0.0.1')
        self.assertEqual(environ['REMOTE_PORT'], '443')

        self.transport.get_extra_info.return_value = '[::1]'
        environ = self._make_one()

        self.assertEqual(environ['REMOTE_ADDR'], '::1')
        self.assertEqual(environ['REMOTE_PORT'], '80')

    def test_wsgi_response(self):
        srv = wsgi.WSGIServerHttpProtocol(self.wsgi)
        srv.stream = self.stream
        srv.transport = self.transport

        resp = srv.create_wsgi_response(self.info, self.message)
        self.assertIsInstance(resp, wsgi.WsgiResponse)

    def test_wsgi_response_start_response(self):
        srv = wsgi.WSGIServerHttpProtocol(self.wsgi)
        srv.stream = self.stream
        srv.transport = self.transport

        resp = srv.create_wsgi_response(self.info, self.message)
        resp.start_response(
            '200 OK', [('CONTENT-TYPE', 'text/plain')])
        self.assertEqual(resp.status, '200 OK')
        self.assertIsInstance(resp.response, protocol.Response)

    def test_wsgi_response_start_response_exc(self):
        srv = wsgi.WSGIServerHttpProtocol(self.wsgi)
        srv.stream = self.stream
        srv.transport = self.transport

        resp = srv.create_wsgi_response(self.info, self.message)
        resp.start_response(
            '200 OK', [('CONTENT-TYPE', 'text/plain')], ['', ValueError()])
        self.assertEqual(resp.status, '200 OK')
        self.assertIsInstance(resp.response, protocol.Response)

    def test_wsgi_response_start_response_exc_status(self):
        srv = wsgi.WSGIServerHttpProtocol(self.wsgi)
        srv.stream = self.stream
        srv.transport = self.transport

        resp = srv.create_wsgi_response(self.info, self.message)
        resp.start_response('200 OK', [('CONTENT-TYPE', 'text/plain')])

        self.assertRaises(
            ValueError,
            resp.start_response,
            '500 Err', [('CONTENT-TYPE', 'text/plain')], ['', ValueError()])

    def test_file_wrapper(self):
        fobj = io.BytesIO(b'data')
        wrapper = wsgi.FileWrapper(fobj, 2)
        self.assertIs(wrapper, iter(wrapper))
        self.assertTrue(hasattr(wrapper, 'close'))

        self.assertEqual(next(wrapper), b'da')
        self.assertEqual(next(wrapper), b'ta')
        self.assertRaises(StopIteration, next, wrapper)

        wrapper = wsgi.FileWrapper(b'data', 2)
        self.assertFalse(hasattr(wrapper, 'close'))

    def test_handle_request_futures(self):

        def wsgi_app(env, start):
            start('200 OK', [('Content-Type', 'text/plain')])
            f1 = tulip.Future()
            f1.set_result(b'data')
            fut = tulip.Future()
            fut.set_result([f1])
            return fut

        srv = wsgi.WSGIServerHttpProtocol(wsgi_app)
        srv.stream = self.stream
        srv.transport = self.transport

        self.loop.run_until_complete(
            srv.handle_request(self.info, self.message))

        content = b''.join(
            [c[1][0] for c in self.transport.write.mock_calls])
        self.assertTrue(content.startswith(b'HTTP/1.0 200 OK'))
        self.assertTrue(content.endswith(b'data'))

    def test_handle_request_simple(self):

        def wsgi_app(env, start):
            start('200 OK', [('Content-Type', 'text/plain')])
            return [b'data']

        stream = tulip.StreamReader()
        stream.feed_data(b'data')
        stream.feed_eof()
        self.message = protocol.RawHttpMessage(
            self.headers, stream, True, 'deflate')
        self.info = protocol.RequestLine('GET', '/path', (1, 1))

        srv = wsgi.WSGIServerHttpProtocol(wsgi_app, True)
        srv.stream = self.stream
        srv.transport = self.transport

        self.loop.run_until_complete(
            srv.handle_request(self.info, self.message))

        content = b''.join(
            [c[1][0] for c in self.transport.write.mock_calls])
        self.assertTrue(content.startswith(b'HTTP/1.1 200 OK'))
        self.assertTrue(content.endswith(b'data\r\n0\r\n\r\n'))

    def test_handle_request_io(self):

        def wsgi_app(env, start):
            start('200 OK', [('Content-Type', 'text/plain')])
            return io.BytesIO(b'data')

        srv = wsgi.WSGIServerHttpProtocol(wsgi_app)
        srv.stream = self.stream
        srv.transport = self.transport

        self.loop.run_until_complete(
            srv.handle_request(self.info, self.message))

        content = b''.join(
            [c[1][0] for c in self.transport.write.mock_calls])
        self.assertTrue(content.startswith(b'HTTP/1.0 200 OK'))
        self.assertTrue(content.endswith(b'data'))