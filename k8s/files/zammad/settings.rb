UserInfo.current_user_id = 1
Setting.set('fqdn', ENV.fetch('ZAMMAD_FQDN'))
Setting.set('http_type', 'https')
Setting.set('product_name', ENV.fetch('ZAMMAD_TITLE'))
Setting.set('user_create_account', false)
Setting.set('auth_openid_connect', true)
Setting.set('auth_openid_connect_credentials', {
  'display_name' => 'Keycloak',
  'identifier' => 'zammad',
  'issuer' => ENV.fetch('ZAMMAD_ISSUER'),
  'uid_field' => 'sub',
  'scope' => 'openid email profile',
  'pkce' => true,
})
# A person who signs in through Keycloak gets the account with their email.
Setting.set('auth_third_party_auto_link_at_inital_login', true)

email = ENV.fetch('SSO_PRIMARY_EMAIL')
admin = User.find_by(email: email) || User.create!(
  login: email, email: email, firstname: 'Platform', lastname: 'Administrator', active: true,
  password: SecureRandom.hex(32), roles: Role.where(name: %w[Admin Agent]))
admin.update!(active: true, roles: (admin.roles.to_a | Role.where(name: %w[Admin Agent]).to_a))
Setting.set('system_init_done', true)

Setting.set('es_url', ENV.fetch('ZAMMAD_ES_URL'))
Setting.set('es_user', ENV.fetch('ELASTICSEARCH_USER'))
Setting.set('es_password', ENV.fetch('ELASTICSEARCH_PASSWORD'))
puts "Zammad settings applied for #{Setting.get('fqdn')}."
