import os

from odoo import api, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model
    def _auth_oauth_signin(self, provider, validation, params):
        # Sign the bootstrap email in as Odoo's existing administrator instead
        # of creating a second, unprivileged user.
        sso_primary_email = os.environ['SSO_PRIMARY_EMAIL']
        if validation.get('email') == sso_primary_email:
            administrator = self.env.ref('base.user_admin').sudo()
            administrator.write({
                'login': sso_primary_email,
                'email': sso_primary_email,
                'oauth_provider_id': provider,
                'oauth_uid': validation['user_id'],
                'oauth_access_token': params['access_token'],
            })
            return administrator.login
        return super()._auth_oauth_signin(provider, validation, params)
