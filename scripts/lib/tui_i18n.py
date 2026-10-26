"""French translation of the setup hub, applied where text reaches whiptail.

English stays the source and the default. `./install.sh --lang fr` (or
SWIRL_CLOUD_LANG=fr) switches the hub to French. Text is translated line by
line: an exact match first, then the patterns for lines that contain values.
Untranslated lines stay in English, so a missing entry never hides information.
"""
import os
import re

LANGUAGE = (os.environ.get("SWIRL_CLOUD_LANG") or "en").lower()[:2]


def set_language(language):
    global LANGUAGE
    LANGUAGE = (language or "en").lower()[:2]


FR = {
    # Buttons and frame
    "Yes": "Oui", "No": "Non", "Back": "Retour", "Quit": "Quitter", "Cancel": "Annuler",
    # Main menu
    "Choose a step. Answers are written only by Save or Install.":
        "Choisissez une étape. Les réponses ne sont écrites que par Enregistrer ou Installer.",
    "What gets installed and the current state": "Ce qui est installé et l'état actuel",
    "Check this host": "Vérifier ce serveur",
    "Configure: express (few questions)": "Configurer : express (quelques questions)",
    "Configure: advanced (every input by section)": "Configurer : avancé (tous les paramètres par section)",
    "Test credentials (Cloudflare, GitHub, Tailscale, SMTP)": "Tester les identifiants (Cloudflare, GitHub, Tailscale, SMTP)",
    "Review answers and problems": "Relire les réponses et les problèmes",
    "Save the input file": "Enregistrer le fichier de paramètres",
    "Plan: installed, missing or drifted stages and the next run":
        "Plan : étapes installées, manquantes ou divergentes et prochaine exécution",
    "Install or update the platform": "Installer ou mettre à jour la plateforme",
    "Add servers": "Ajouter des serveurs",
    "Import and deploy repositories (GitHub, or existing GitLab projects)":
        "Importer et déployer des dépôts (GitHub, ou projets GitLab existants)",
    "Deploy an application to int, uat or prod": "Déployer une application en int, uat ou prod",
    "Platform status and verification": "État et vérification de la plateforme",
    "Service links": "Liens des services",
    "Maintenance: backup, unseal, password rotation, audits, logs":
        "Maintenance : sauvegarde, descellement, rotation des mots de passe, audits, journaux",
    "Files, commands and guides": "Fichiers, commandes et guides",
    # Overview
    "Swirl Cloud turns this server into a private cloud:": "Swirl Cloud transforme ce serveur en cloud privé :",
    "Cluster     K3s, Longhorn storage, Traefik ingress, optional extra servers":
        "Cluster     K3s, stockage Longhorn, entrée Traefik, serveurs supplémentaires en option",
    "Security    Vault + External Secrets, Keycloak SSO, Cloudflare DNS/TLS/Access,":
        "Sécurité    Vault + External Secrets, SSO Keycloak, DNS/TLS/Access Cloudflare,",
    "host firewall, Fail2ban/CrowdSec on internet-exposed servers":
        "pare-feu hôte, Fail2ban/CrowdSec sur les serveurs exposés à Internet",
    "Data        PostgreSQL, Kafka, Redis and MongoDB shared by applications":
        "Données     PostgreSQL, Kafka, Redis et MongoDB partagés par les applications",
    "Delivery    GitLab with CI runners and registry, Argo CD, SonarQube":
        "Livraison   GitLab avec runners CI et registre, Argo CD, SonarQube",
    "Operations  Prometheus, Grafana, Elasticsearch/Kibana, Homepage portal,":
        "Exploitation Prometheus, Grafana, Elasticsearch/Kibana, portail Homepage,",
    "nightly backups (optionally encrypted in S3)": "sauvegardes nocturnes (chiffrées dans S3 en option)",
    'Business    Odoo ERP (scope "apps")': 'Gestion     ERP Odoo (périmètre "apps")',
    "Steps: Check this host -> Configure (express or advanced) -> Test credentials":
        "Étapes : Vérifier ce serveur -> Configurer (express ou avancé) -> Tester les identifiants",
    "-> Install. Answers are saved to the input file shown at the top; nothing":
        "-> Installer. Les réponses sont enregistrées dans le fichier indiqué en haut ; rien",
    "changes on the host until you choose Install.": "ne change sur le serveur avant de choisir Installer.",
    "Current state": "État actuel",
    "Files and commands": "Fichiers et commandes",
    "Guides": "Guides",
    # Sections
    "Identity": "Identité", "Repositories": "Dépôts", "Exposure and scope": "Exposition et périmètre",
    "Nodes": "Nœuds", "Components": "Composants", "Backups": "Sauvegardes", "Email": "E-mail", "Public access": "Accès public",
    "Administrators": "Administrateurs",
    # Inputs
    "Company or organization display name": "Nom affiché de l'entreprise ou de l'organisation",
    "Shown in the portal, GitLab and Access; spaces and punctuation are allowed.":
        "Affiché dans le portail, GitLab et Access ; espaces et ponctuation autorisés.",
    "Public base domain for this cluster": "Domaine public de base de ce cluster",
    "For example example.com. Services use subdomains of it.":
        "Par exemple exemple.fr. Les services en utilisent des sous-domaines.",
    "Organization identifier (lowercase DNS label)": "Identifiant de l'organisation (libellé DNS en minuscules)",
    "Defaults to the first label of the public domain.": "Par défaut, le premier libellé du domaine public.",
    "Private service DNS zone": "Zone DNS privée des services",
    "Defaults to internal.<public domain>.": "Par défaut internal.<domaine public>.",
    "GitLab group path": "Chemin du groupe GitLab",
    "Defaults to the organization identifier.": "Par défaut, l'identifiant de l'organisation.",
    "GitLab group display name": "Nom affiché du groupe GitLab",
    "Defaults to the organization name.": "Par défaut, le nom de l'organisation.",
    "Platform GitLab project name": "Nom du projet GitLab de la plateforme",
    "Defaults to swirl-cloud.": "Par défaut swirl-cloud.",
    "Application SSO realm": "Royaume SSO des applications",
    "Shared TLS secret name": "Nom du secret TLS partagé",
    "SonarQube GitLab integration name": "Nom de l'intégration SonarQube GitLab",
    "Cloudflare Access identity provider name": "Nom du fournisseur d'identité Cloudflare Access",
    "Cloudflare Zero Trust team label": "Libellé de l'équipe Cloudflare Zero Trust",
    "Kubernetes name of this node": "Nom Kubernetes de ce nœud",
    "Defaults to the lowercase host name.": "Par défaut, le nom d'hôte en minuscules.",
    "Cluster GitOps repository URL (.git)": "URL du dépôt GitOps du cluster (.git)",
    "Import GitHub repositories into GitLab with two-way sync?":
        "Importer des dépôts GitHub dans GitLab avec synchronisation bidirectionnelle ?",
    "Repository source": "Source des dépôts",
    "GitLab projects to deploy (comma-separated)": "Projets GitLab à déployer (séparés par des virgules)",
    "GitHub username": "Nom d'utilisateur GitHub",
    "GitHub fine-grained personal access token": "Jeton d'accès personnel GitHub à granularité fine",
    "Repositories to import (comma-separated)": "Dépôts à importer (séparés par des virgules)",
    "Repositories to deploy: all, none, or comma-separated names":
        "Dépôts à déployer : all, none ou noms séparés par des virgules",
    "Deployment environments inventory file (optional)": "Fichier d'inventaire des environnements (facultatif)",
    "Is this server internet-exposed or local-only?": "Ce serveur est-il exposé à Internet ou uniquement local ?",
    "Deployment scope": "Périmètre du déploiement",
    "apps also installs Odoo in corp; infra omits it.": "apps installe aussi Odoo dans corp ; infra ne l'installe pas.",
    "Control-plane nodes including this host (1, 3, 5, ...)": "Nœuds du plan de contrôle, ce serveur compris (1, 3, 5, ...)",
    "Desired final count, including registered nodes; must be odd.":
        "Nombre final souhaité, nœuds enregistrés compris ; doit être impair.",
    "Total cluster nodes including control planes": "Nombre total de nœuds, plans de contrôle compris",
    "Allow control planes to run workloads?": "Autoriser les plans de contrôle à exécuter des charges ?",
    "Defaults to true without workers, otherwise false.": "Par défaut oui sans nœuds de travail, sinon non.",
    "Private node transport": "Transport privé entre nœuds",
    "SSH user for additional control planes": "Utilisateur SSH des plans de contrôle supplémentaires",
    "SSH user for workers": "Utilisateur SSH des nœuds de travail",
    "SSH port for additional control planes": "Port SSH des plans de contrôle supplémentaires",
    "SSH port for workers": "Port SSH des nœuds de travail",
    "SSH private key for additional control planes": "Clé privée SSH des plans de contrôle supplémentaires",
    "SSH private key for workers": "Clé privée SSH des nœuds de travail",
    "Monthly cost of this server for cost showback (optional)":
        "Coût mensuel de ce serveur pour la refacturation (facultatif)",
    "Rent, or purchase price spread over its service life plus hosting. Blank skips cost showback.":
        "Loyer, ou prix d'achat réparti sur la durée de vie plus l'hébergement. Vide : pas de refacturation.",
    "Currency of server costs (ISO 4217 code)": "Devise des coûts des serveurs (code ISO 4217)",
    "Activate the opt-in high-availability profile?": "Activer le profil de haute disponibilité ?",
    "Install/upgrade system prerequisites?": "Installer ou mettre à jour les prérequis système ?",
    "Also install developer tools (Java 21, Maven, Docker)?": "Installer aussi les outils de développement (Java 21, Maven, Docker) ?",
    "Install or reinstall the K3s control plane?": "Installer ou réinstaller le plan de contrôle K3s ?",
    "Defaults to true only when K3s is not installed.": "Par défaut oui seulement si K3s n'est pas installé.",
    "Install/upgrade Longhorn storage?": "Installer ou mettre à jour le stockage Longhorn ?",
    "Install/upgrade the Traefik ingress controller?": "Installer ou mettre à jour le contrôleur d'entrée Traefik ?",
    "Install/upgrade Vault and External Secrets?": "Installer ou mettre à jour Vault et External Secrets ?",
    "Deploy/upgrade data stores (PostgreSQL, Kafka, Redis, MongoDB)?":
        "Déployer ou mettre à jour les bases de données (PostgreSQL, Kafka, Redis, MongoDB) ?",
    "Deploy/upgrade platform services?": "Déployer ou mettre à jour les services de la plateforme ?",
    "Install/upgrade Descheduler resources?": "Installer ou mettre à jour le Descheduler ?",
    "Install/upgrade Argo CD?": "Installer ou mettre à jour Argo CD ?",
    "Configure encrypted off-node backups in S3-compatible storage?":
        "Configurer des sauvegardes chiffrées hors du serveur vers un stockage compatible S3 ?",
    "S3-compatible HTTPS endpoint": "Point d'accès HTTPS compatible S3",
    "S3 region": "Région S3", "Private backup bucket": "Bucket privé de sauvegarde",
    "S3 access-key ID": "Identifiant de clé d'accès S3", "S3 secret access key": "Clé d'accès secrète S3",
    "Recovery-archive encryption password (16+ characters)":
        "Mot de passe de chiffrement des archives de reprise (16 caractères ou plus)",
    "Keep a copy outside this cluster; restores need it.": "Gardez-en une copie hors de ce cluster ; les restaurations en ont besoin.",
    "Send platform email (alerts, password resets) through an SMTP relay?":
        "Envoyer les e-mails de la plateforme (alertes, réinitialisations de mot de passe) par un relais SMTP ?",
    "Alerts, Keycloak password resets, GitLab and Odoo use it; see docs/guides/platform-mail.md.":
        "Les alertes, les réinitialisations de mot de passe Keycloak, GitLab et Odoo l'utilisent ; voir docs/guides/platform-mail.md.",
    "SMTP relay host": "Serveur du relais SMTP", "For example smtp.example.com.": "Par exemple smtp.example.com.",
    "SMTP TLS mode": "Mode TLS SMTP",
    "ssl is implicit TLS (usually port 465); starttls usually uses port 587.":
        "ssl est le TLS implicite (en général port 465) ; starttls utilise en général le port 587.",
    "SMTP relay port": "Port du relais SMTP",
    "Defaults to 465 for ssl and 587 for starttls.": "Par défaut 465 pour ssl et 587 pour starttls.",
    "SMTP username": "Identifiant SMTP", "SMTP password or API key": "Mot de passe SMTP ou clé d'API",
    "Sender address": "Adresse d'expéditeur",
    "Defaults to cloud@<public domain>; the relay must accept it as a sender.":
        "Par défaut cloud@<domaine public> ; le relais doit l'accepter comme expéditeur.",
    "Sender display name": "Nom d'expéditeur affiché", "Defaults to '<organization> Cloud'.": "Par défaut « <organisation> Cloud ».",
    "Alert recipients (comma-separated)": "Destinataires des alertes (séparés par des virgules)",
    "Critical and warning alerts are emailed here; defaults to the platform administrator's email.":
        "Les alertes critiques et d'avertissement y sont envoyées ; par défaut l'e-mail de l'administrateur de la plateforme.",
    "Configure Cloudflare public DNS, TLS and Access?": "Configurer le DNS public, le TLS et Access de Cloudflare ?",
    "Defaults to true for internet-exposed servers.": "Par défaut oui pour les serveurs exposés à Internet.",
    "Cloudflare User API Token (cfut_...)": "Jeton d'API utilisateur Cloudflare (cfut_...)",
    "Protect administration hosts with Cloudflare Access?": "Protéger les sites d'administration avec Cloudflare Access ?",
    "Emails allowed into administration UIs (space-separated)":
        "Adresses e-mail autorisées sur les interfaces d'administration (séparées par des espaces)",
    "Platform administrator login (username or email)": "Identifiant de l'administrateur de la plateforme (nom ou e-mail)",
    "Platform administrator password": "Mot de passe de l'administrateur de la plateforme",
    "12+ characters with lowercase, uppercase, digit and symbol.":
        "12 caractères ou plus avec minuscule, majuscule, chiffre et symbole.",
    "Use one password for all local infrastructure superusers?":
        "Utiliser un seul mot de passe pour tous les super-utilisateurs locaux ?",
    "Local infrastructure administrator password": "Mot de passe administrateur local de l'infrastructure",
    # Express, advanced, review, install
    "Express asks only what a default installation needs. Change anything later under Advanced.":
        "Le mode express ne pose que les questions d'une installation par défaut. Modifiez le reste dans Avancé.",
    "Express configuration complete.": "Configuration express terminée.",
    "Next: Test credentials, then Install.": "Ensuite : tester les identifiants, puis installer.",
    "Each section walks every input that applies to your answers.":
        "Chaque section parcourt les paramètres qui s'appliquent à vos réponses.",
    "No problems found.": "Aucun problème trouvé.",
    "Fix these first (Review lists them too):": "Corrigez d'abord ceci (également listé dans Relire) :",
    "Still missing or invalid:": "Encore manquant ou invalide :",
    "Install anyway?": "Installer quand même ?",
    "Install or update the platform now?": "Installer ou mettre à jour la plateforme maintenant ?",
    "This changes this host: packages, firewall, K3s and every selected platform service. A first installation takes "
    "30-60 minutes. Reruns keep existing data.":
        "Cela modifie ce serveur : paquets, pare-feu, K3s et chaque service sélectionné. Une première installation "
        "prend 30 à 60 minutes. Les réexécutions conservent les données.",
    "Installation complete and verified.": "Installation terminée et vérifiée.",
    "Installation finished, but verification found problems:": "Installation terminée, mais la vérification a trouvé des problèmes :",
    "What the next run will do": "Ce que fera la prochaine exécution",
    "Leave empty to keep the current value.": "Laissez vide pour garder la valeur actuelle.",
    "Leave empty for the default.": "Laissez vide pour la valeur par défaut.",
    "This host has blocking problems:": "Ce serveur a des problèmes bloquants :",
    "Type the value": "Saisir la valeur",
    "Read it from a protected file (keeps it out of the input file)":
        "La lire depuis un fichier protégé (hors du fichier de paramètres)",
    "Generate a strong password": "Générer un mot de passe robuste",
    "One line, owned by you, mode 600.": "Une ligne, vous appartenant, mode 600.",
    "Record it in your password manager now. It is saved in the input file and not shown again.":
        "Enregistrez-le maintenant dans votre gestionnaire de mots de passe. Il est sauvegardé dans le fichier et ne sera plus affiché.",
    "The values do not match. Try again.": "Les valeurs ne correspondent pas. Réessayez.",
    "a path is required": "un chemin est requis",
    "Add more servers to this cluster during installation?": "Ajouter d'autres serveurs à ce cluster pendant l'installation ?",
    "Choose No for one server; you can add servers later from the main menu.":
        "Choisissez Non pour un seul serveur ; vous pourrez en ajouter depuis le menu principal.",
    "Save your answers before quitting?": "Enregistrer vos réponses avant de quitter ?",
    "The platform is not installed on this host yet.": "La plateforme n'est pas encore installée sur ce serveur.",
    "Install the platform first; repositories are imported into its GitLab.":
        "Installez d'abord la plateforme ; les dépôts sont importés dans son GitLab.",
    "Install the platform on this server first; it enrolls the new servers.":
        "Installez d'abord la plateforme sur ce serveur ; il inscrit les nouveaux serveurs.",
    "Role of this machine": "Rôle de cette machine",
    "Enroll new control planes from this server over SSH": "Inscrire de nouveaux plans de contrôle depuis ce serveur par SSH",
    "Enroll new workers from this server over SSH": "Inscrire de nouveaux nœuds de travail depuis ce serveur par SSH",
    "Join THIS machine to an existing cluster": "Joindre CETTE machine à un cluster existant",
    "Host checks": "Vérifications du serveur", "Credential checks": "Vérification des identifiants",
    # Deploy
    "Current deployments": "Déploiements actuels",
    "What to deploy": "Que déployer",
    "No environment-aware applications are registered yet.": "Aucune application multi-environnement n'est encore enregistrée.",
    "Branch or tag to build and deploy (empty: default branch)":
        "Branche ou étiquette à construire et déployer (vide : branche par défaut)",
    "Release version to promote (for example 1.2.3)": "Version publiée à promouvoir (par exemple 1.2.3)",
    "Publish a new release from the default branch": "Publier une nouvelle version depuis la branche par défaut",
    "Promote an existing release version": "Promouvoir une version existante",
    "The release gate is waiting for approval in GitLab; the log shows its link.":
        "La validation de version attend une approbation dans GitLab ; le journal en donne le lien.",
    "The cluster API is not reachable from this account yet.": "L'API du cluster n'est pas encore joignable depuis ce compte.",
    "Sign in with the platform administrator login.": "Connectez-vous avec l'identifiant administrateur de la plateforme.",
    "Other environments": "Autres environnements",
    # Maintenance
    "Maintenance": "Maintenance",
    "Run the recovery backup now": "Lancer la sauvegarde de reprise maintenant",
    "Unseal Vault now": "Desceller Vault maintenant",
    "Apply Kubernetes API audit logging now (restarts K3s)": "Activer maintenant l'audit de l'API Kubernetes (redémarre K3s)",
    "Rotate local infrastructure administrator passwords": "Renouveler les mots de passe administrateur locaux",
    "Audit cluster hosts with Lynis": "Auditer les serveurs du cluster avec Lynis",
    "Validate rendered profiles against the API server": "Valider les profils générés auprès du serveur d'API",
    "Failed host maintenance units": "Unités de maintenance en échec",
    "Earlier run logs": "Journaux des exécutions précédentes",
    "Create a support bundle (secrets redacted)": "Créer une archive de support (secrets masqués)",
    "Compliance report and evidence pack": "Rapport de conformité et dossier de preuves",
    "Access review sheet (who has access to what)": "Revue des accès (qui a accès à quoi)",
    "Record this server's monthly cost (cost showback)": "Enregistrer le coût mensuel de ce serveur (refacturation)",
    "Export the encrypted recovery kit": "Exporter le kit de reprise chiffré",
    "Verify a recovery kit against the current keys": "Vérifier un kit de reprise avec les clés actuelles",
    "Install the API audit policy and restart K3s if needed?":
        "Installer la politique d'audit de l'API et redémarrer K3s si nécessaire ?",
    "The Kubernetes API is unavailable for about a minute; running workloads keep running. Prefer a maintenance window "
    "on single-server clusters.":
        "L'API Kubernetes est indisponible environ une minute ; les charges en cours continuent. Préférez une fenêtre "
        "de maintenance sur un cluster à un seul serveur.",
    "Restart K3s": "Redémarrer K3s",
    "Separate keys": "Séparer les clés", "Keep in archives": "Garder dans les archives",
    "Path of the recovery kit to verify": "Chemin du kit de reprise à vérifier",
    "Currency (ISO 4217 code)": "Devise (code ISO 4217)",
    "Where the price comes from, for example the offer or invoice (optional)":
        "Origine du prix, par exemple l'offre ou la facture (facultatif)",
    "No run logs yet.": "Aucun journal d'exécution pour l'instant.",
    "Run logs (newest first)": "Journaux d'exécution (plus récents d'abord)",
    "Support bundle written:": "Archive de support écrite :",
    "The support bundle failed:": "L'archive de support a échoué :",
    "Failed swirl-cloud units:": "Unités swirl-cloud en échec :",
    "Unattended reruns:": "Réexécutions sans intervention :",
    # Checks
    "Operating system": "Système d'exploitation", "Architecture": "Architecture", "CPU": "Processeur",
    "Memory": "Mémoire", "Free disk": "Disque libre", "Internet egress": "Accès sortant à Internet",
    "Cloudflare token": "Jeton Cloudflare", "GitHub token": "Jeton GitHub", "Tailscale token": "Jeton Tailscale",
    "OVHcloud API keys": "Clés d'API OVHcloud", "S3 backup keys": "Clés S3 de sauvegarde",
    "downloads are reachable": "les téléchargements sont joignables",
    "80, 443 and 6443 are free": "80, 443 et 6443 sont libres",
    "no Cloudflare, GitHub, Tailscale or SMTP credentials entered yet": "aucun identifiant Cloudflare, GitHub, Tailscale ou SMTP saisi",
    "can list tailnet devices": "peut lister les appareils du tailnet",
    "accessible with admin rights": "accessible avec les droits d'administration",
}

# Lines that contain values: (English pattern, French template).
PATTERNS = [
    (r"Swirl Cloud setup  \|  (.+)", r"Installation Swirl Cloud  |  \1"),
    (r"(\s*)K3s on this host:  installed", r"\1K3s sur ce serveur :  installé"),
    (r"(\s*)K3s on this host:  not installed", r"\1K3s sur ce serveur :  non installé"),
    (r"(\s*)Cluster nodes:     (.*)", r"\1Nœuds du cluster :   \2"),
    (r"(\s*)Input file:        (.*) \(saved\)", r"\1Fichier :           \2 (enregistré)"),
    (r"(\s*)Input file:        (.*) \(not saved yet\)", r"\1Fichier :           \2 (pas encore enregistré)"),
    (r"(\s*)Answers:           (.*)", r"\1Réponses :          \2"),
    (r"(\s*)Open problems:     (.*)", r"\1Problèmes ouverts : \2"),
    (r"Saved and validated (.+)\.", r"\1 enregistré et validé."),
    (r"Saved (.+), but the installer rejected it:", r"\1 enregistré, mais l'installateur l'a refusé :"),
    (r"The installation stopped \(exit code (\d+)\)\. Last lines:", r"L'installation s'est arrêtée (code \1). Dernières lignes :"),
    (r"Full log: (.+)", r"Journal complet : \1"),
    (r"Enrollment stopped \(exit code (\d+)\)\. Last lines:", r"L'inscription s'est arrêtée (code \1). Dernières lignes :"),
    (r"Repository import stopped \(exit code (\d+)\)\. Last lines:", r"L'import des dépôts s'est arrêté (code \1). Dernières lignes :"),
    (r"The deployment did not complete \(exit code (\d+)\)\. Last lines:", r"Le déploiement n'a pas abouti (code \1). Dernières lignes :"),
    (r"(.+) finished with exit code (\d+)\.", r"\1 terminé avec le code \2."),
    (r"(.+) is not installed on this host\.", r"\1 n'est pas installé sur ce serveur."),
    (r"Environment for (.+)", r"Environnement pour \1"),
    (r"Deploy (.+) to (.+)", r"Déployer \1 vers \2"),
    (r"Portal: (.+)", r"Portail : \1"),
    (r"(\d+) cores \(8\+ recommended\)", r"\1 cœurs (8 ou plus recommandés)"),
    (r"(\d+) GiB \(24\+ GiB recommended for every platform service\)", r"\1 Gio (24 Gio ou plus recommandés pour tous les services)"),
    (r"(\d+) GiB under /var/lib \(100\+ GiB recommended\)", r"\1 Gio sous /var/lib (100 Gio ou plus recommandés)"),
    (r"authenticates as (.+)", r"authentifié en tant que \1"),
    (r"not accepted \(HTTP (\d+)\)", r"refusé (HTTP \1)"),
    (r"Rotation stopped \(exit code (\d+)\); rerun resumes with START_AT\. Last lines:",
     r"Rotation arrêtée (code \1) ; relancer reprend avec START_AT. Dernières lignes :"),
    (r"Path of the file holding (.+)\.", r"Chemin du fichier contenant \1."),
]
COMPILED = [(re.compile(pattern + r"\Z"), template) for pattern, template in PATTERNS]


def translate_line(line):
    stripped = line.strip()
    if not stripped:
        return line
    indent = line[:len(line) - len(line.lstrip())]
    if stripped in FR:
        return indent + FR[stripped]
    if stripped.startswith("Confirm: "):
        rest = stripped.removeprefix("Confirm: ")
        return f"{indent}Confirmer : {FR.get(rest, rest)}"
    for pattern, template in COMPILED:
        if pattern.match(line):
            return pattern.sub(template, line)
    return line


def t(text):
    """Translate hub text for the selected language; English is returned unchanged."""
    if LANGUAGE != "fr" or not text:
        return text
    if text in FR:
        return FR[text]
    return "\n".join(translate_line(line) for line in text.split("\n"))
