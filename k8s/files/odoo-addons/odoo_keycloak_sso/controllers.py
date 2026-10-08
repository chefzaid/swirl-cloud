from urllib.parse import urlencode

from odoo import http
from odoo.http import request
from odoo.addons.auth_oauth.controllers.main import OAuthLogin


class KeycloakAutoLogin(OAuthLogin):
    @http.route('/web/login', type='http', auth='none')
    def web_login(self, *args, **kwargs):
        local_login = kwargs.get('local') == '1'
        oauth_error = kwargs.get('oauth_error')
        if request.httprequest.method == 'GET' and not request.session.uid and not local_login and not oauth_error:
            providers = self.list_providers()
            if providers:
                # Odoo's redirect helper treats destinations as local by
                # default and strips the Keycloak origin. OAuth providers
                # are explicitly external destinations.
                return request.redirect(providers[0]['auth_link'], 303, local=False)
        return super().web_login(*args, **kwargs)

    @http.route('/web/session/logout', type='http', auth='none')
    def logout(self, redirect='/web', **kwargs):
        request.session.logout(keep_db=True)
        query = urlencode({
            'client_id': 'odoo',
            'post_logout_redirect_uri': 'https://odoo.__PUBLIC_DOMAIN__/',
        })
        return request.redirect(
            'https://keycloak.__PUBLIC_DOMAIN__/auth/realms/__KEYCLOAK_REALM__/protocol/openid-connect/logout?' + query,
            303,
            local=False,
        )
