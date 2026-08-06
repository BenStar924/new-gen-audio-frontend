/* Embedded HTTP server: serves the vendored front end out of www/ and
 * implements the /api endpoints the UI polls and posts to. */

#ifndef OPERATORCONSOLE_WEBSERVER_H
#define OPERATORCONSOLE_WEBSERVER_H

/* Binds to loopback only. The production device is offline and the browser is
 * on the same board, so there is no reason to accept a connection from
 * anywhere else — and every reason not to. */
int  webserver_start(unsigned short port, const char *www_root);
void webserver_stop(void);

#endif /* OPERATORCONSOLE_WEBSERVER_H */
