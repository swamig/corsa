try:
    import urllib.parse as urlparse
except ImportError:
    # py2
    import urlparse

import tornado.httpclient
import tornado.web

# headers to remove as of HTTP 1.1 RFC2616
# http://www.w3.org/Protocols/rfc2616/rfc2616-sec13.html
hop_by_hop_headers = set([
    'connection',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailers',
    'transfer-encoding',
    'upgrade',
])


class ProxyHandler(tornado.web.RequestHandler):
    def __init__(self, *args, **kw):
        self.proxy_whitelist = kw.pop('proxy_whitelist', None)
        self.origin_whitelist = kw.pop('origin_whitelist', None)
        super(ProxyHandler, self).__init__(*args, **kw)

    def check_proxy_host(self, url_parts):
        if self.proxy_whitelist is None:
            return

        url = '%s://%s' % (url_parts.scheme, url_parts.netloc)

        if url in self.proxy_whitelist:
            return

        raise tornado.web.HTTPError(403)

    def check_origin(self):
        if self.origin_whitelist is None:
            return

        if 'Origin' not in self.request.headers:
            raise tornado.web.HTTPError(403)

        if self.request.headers['Origin'] not in self.origin_whitelist:
            raise tornado.web.HTTPError(403)

    def response_handler(self, response):
        if response.error and not isinstance(response.error, tornado.httpclient.HTTPError):
            self.set_status(500)
            self.write('Internal server error:\n' + str(response.error))
            self.finish()
            return

        if response.code == 599:
            # connection closed
            self.set_status(502)
            self.write('Bad gateway:\n' + str(response.error))
            self.finish()
            return

        self.set_status(response.code)
        # copy all but hop-by-hop headers
        for header, v in response.headers.items():
            if header.lower() not in hop_by_hop_headers:
                self.set_header(header, v)

        origin = self.request.headers.get('Origin', '*')
        self.set_header('Access-Control-Allow-Origin', origin)

        if self.request.method == 'OPTIONS':
            if 'Access-Control-Request-Headers' in self.request.headers:
                # allow all requested headers
                self.set_header('Access-Control-Allow-Headers',
                    self.request.headers.get('Access-Control-Request-Headers', ''))

            self.set_header('Access-Control-Allow-Methods',
                response.headers.get('Allow', ''))

            if response.code == 405:
                # fake OPTIONS response when source doesn't support it
                # as OPTIONS is required for CORS preflight requests.
                # the 405 response should contain the supported methods
                # in the Allow header.
                self.set_status(200)
                self.clear_header('Content-Length')
                self.finish()
                return

        if response.body:
            self.write(response.body)
        self.finish()

    @tornado.web.asynchronous
    def request_handler(self, url):
        url_parts = urlparse.urlparse(url)
        self.request.headers['Host'] = url_parts.netloc

        self.check_proxy_host(url_parts)
        self.check_origin()

        if self.request.query:
            url = url + '?' + self.request.query
        req = tornado.httpclient.HTTPRequest(
            url=url,
            method=self.request.method,
            body=self.request.body,
            headers=self.request.headers,
            follow_redirects=False,
            allow_nonstandard_methods=True,
            validate_cert=False, #for ssl dev servers that are using free dev certs
            use_gzip=False, # otherwise tornado will decode proxied data
        )

        client = tornado.httpclient.AsyncHTTPClient()
        try:
            client.fetch(req, self.response_handler)
        except tornado.httpclient.HTTPError as e:
            # pass regular HTTP errors from server to client
            if hasattr(e, 'response') and e.response:
                self.response_handler(e.response)
            else:
                raise

    # alias HTTP methods to generic request handler
    SUPPORTED_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS']
    get = request_handler
    post = request_handler
    put = request_handler
    delete = request_handler
    head = request_handler
    options = request_handler
