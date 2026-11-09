{
    "name": "Platform CRM Intake",
    "summary": "The company website's CRM lead intake, Live Chat frame and chat emails",
    "version": "19.0.1.0.0",
    "author": "Platform",
    "license": "LGPL-3",
    "depends": ["crm", "im_livechat"],
    "data": [
        "data/res_groups.xml",
        "data/ir_cron.xml",
        "views/chat_frame.xml",
        "views/chat_emails.xml",
    ],
    "assets": {
        # Loaded with every external chat embed; the scripts act only inside the
        # website's chat frame, where the page sets platformWebsiteOrigin. The
        # theme applies to every embed: Live Chat serves only the website.
        "im_livechat.assets_embed_external": [
            ("before", "web/static/src/scss/primary_variables.scss",
             "platform_crm_intake/static/src/chat_theme_variables.scss"),
            "platform_crm_intake/static/src/website_frame_service.js",
            "platform_crm_intake/static/src/chat_window_status.js",
            "platform_crm_intake/static/src/chat_window_status.xml",
            "platform_crm_intake/static/src/chat_window_status.scss",
            "platform_crm_intake/static/src/chat_theme.scss",
        ],
    },
    "installable": True,
    "application": False,
}
