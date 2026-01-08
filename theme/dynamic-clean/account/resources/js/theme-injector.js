(function () {
    const realmMatch = window.location.pathname.match(/\/realms\/([^/]+)/);
    if (!realmMatch) return;

    const realm = realmMatch[1];

    // --- 1. Parse URL Parameters (OIDC Hints) ---
    const urlParams = new URLSearchParams(window.location.search);

    const getParam = (key) => {
        if (urlParams.has(key)) return urlParams.get(key);
        const redirectUri = urlParams.get('redirect_uri');
        if (redirectUri) {
            try {
                const nestedUrl = new URL(redirectUri.startsWith('http') ? redirectUri : window.location.origin + redirectUri);
                const nestedParams = new URLSearchParams(nestedUrl.search);
                if (nestedParams.has(key)) return nestedParams.get(key);
            } catch (e) { }
        }
        return null;
    };

    let urlLocale = getParam('ui_locales') || getParam('kc_locale') || 'en';
    const urlThemeOverride = getParam('ui_theme');

    if (urlLocale === 'iw' || urlLocale.startsWith('he')) {
        urlLocale = 'he';
    }

    const configApi = `/v1/themes/${realm}`;

    fetch(configApi)
        .then(response => response.json())
        .then(config => {
            const root = document.documentElement;
            const translations = config.translations && config.translations[urlLocale] ? config.translations[urlLocale] : {};

            // RTL
            if (urlLocale === 'he' || urlLocale === 'ar') {
                document.body.dir = 'rtl';
                document.documentElement.setAttribute('dir', 'rtl');
                document.documentElement.classList.add('rtl');
            }

            // Styling
            if (config.primaryColor) root.style.setProperty('--primary-color', config.primaryColor);
            if (config.secondaryColor) root.style.setProperty('--secondary-color', config.secondaryColor);
            if (config.backgroundColor) root.style.setProperty('--background-color', config.backgroundColor);
            if (config.borderRadius) root.style.setProperty('--border-radius', config.borderRadius + 'px');
            if (config.fontFamily) root.style.setProperty('--font-family', config.fontFamily);
            if (config.logoUrl) root.style.setProperty('--logo-url', `url(${config.logoUrl})`);
            if (config.cardBg) root.style.setProperty('--card-bg', config.cardBg);

            // Theme Mode
            const modeToApply = urlThemeOverride || config.themeMode || 'system';
            const applyMode = (mode) => {
                if (mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
                    document.documentElement.classList.add('dark-mode');
                    document.documentElement.classList.remove('light-mode');
                } else {
                    document.documentElement.classList.add('light-mode');
                    document.documentElement.classList.remove('dark-mode');
                }
            };
            applyMode(modeToApply);

            // Background
            if (config.backgroundUrl) {
                document.body.style.setProperty('background', `url(${config.backgroundUrl}) no-repeat center center fixed`, 'important');
                document.body.style.setProperty('background-size', 'cover', 'important');
            }

            // Custom CSS
            if (config.customCss) {
                const style = document.createElement('style');
                style.textContent = config.customCss;
                document.head.appendChild(style);
            }

            // Text Overrides
            const titleElem = document.getElementById('kc-page-title') || document.querySelector('h1.pf-c-title');
            if (titleElem) titleElem.innerText = translations.loginTitle || config.loginTitle || titleElem.innerText;

            const btn = document.getElementById('kc-login');
            if (btn) {
                const btnText = translations.loginButtonText || config.loginButtonText;
                if (btnText) {
                    if (btn.tagName === 'INPUT') btn.value = btnText;
                    else btn.innerText = btnText;
                }
            }

            // Labels
            const fieldMapping = {
                'username': translations.emailLabel,
                'password': translations.passwordLabel
            };
            for (const [id, text] of Object.entries(fieldMapping)) {
                if (text) {
                    const label = document.querySelector(`label[for="${id}"]`);
                    if (label) label.innerText = text;
                }
            }

            // Footer
            const footerText = translations.footerText || config.footerText;
            if (footerText) {
                let footer = document.getElementById('custom-footer') || document.createElement('div');
                footer.id = 'custom-footer';
                footer.style.marginTop = '20px';
                footer.style.opacity = '0.7';
                footer.innerHTML = footerText;
                const card = document.querySelector('.card-pf');
                if (card && !document.getElementById('custom-footer')) card.appendChild(footer);
            }
        });
})();
