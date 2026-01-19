(function () {
    const realmMatch = window.location.pathname.match(/\/realms\/([^/]+)/);
    if (!realmMatch) return;

    const realm = realmMatch[1];
    let lastValidConfig = {}; // State for rollback

    // --- 1. Helper: URL Parameter Parsing ---
    const getParam = (key) => {
        const urlParams = new URLSearchParams(window.location.search);
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
    if (urlLocale === 'iw' || urlLocale.startsWith('he')) urlLocale = 'he';
    const urlThemeOverride = getParam('ui_theme');

    // --- 2. Core Logic: Apply Theme Config ---
    const applyTheme = (config) => {
        try {
            const root = document.documentElement;
            const translations = config.translations && config.translations[urlLocale] ? config.translations[urlLocale] : {};

            // RTL Support
            if (urlLocale === 'he' || urlLocale === 'ar') {
                document.body.dir = 'rtl';
                document.documentElement.setAttribute('dir', 'rtl');
                document.documentElement.classList.add('rtl');
            }

            // Styling Variables
            const setVar = (key, val) => val ? root.style.setProperty(key, val) : root.style.removeProperty(key);
            setVar('--primary-color', config.primaryColor);
            setVar('--secondary-color', config.secondaryColor);
            setVar('--background-color', config.backgroundColor);
            setVar('--border-radius', config.borderRadius ? config.borderRadius + 'px' : null);
            setVar('--font-family', config.fontFamily);
            setVar('--logo-url', config.logoUrl ? `url(${config.logoUrl})` : null);
            setVar('--card-bg', config.cardBg);

            // Theme Mode (Dark/Light/System)
            const modeToApply = urlThemeOverride || config.themeMode || 'system';
            if (modeToApply === 'dark' || (modeToApply === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
                root.classList.add('dark-mode');
                root.classList.remove('light-mode');
            } else {
                root.classList.add('light-mode');
                root.classList.remove('dark-mode');
            }

            // Body Background
            if (config.backgroundUrl) {
                document.body.style.setProperty('background', `url(${config.backgroundUrl}) no-repeat center center fixed`, 'important');
                document.body.style.setProperty('background-size', 'cover', 'important');
            }

            // Custom CSS Injection (with deduplication)
            const existingStyle = document.getElementById('theme-custom-css');
            if (existingStyle) existingStyle.remove();

            if (config.customCss) {
                const style = document.createElement('style');
                style.id = 'theme-custom-css';
                style.textContent = config.customCss;
                document.head.appendChild(style);
            }

            // Text Overrides
            const createOrUpdate = (selector, text) => {
                const el = document.querySelector(selector);
                if (el && text) {
                    if (el.tagName === 'INPUT') el.value = text;
                    else el.innerText = text;
                }
            };

            createOrUpdate('#kc-page-title, h1.pf-c-title', translations.loginTitle || config.loginTitle);
            createOrUpdate('#kc-login', translations.loginButtonText || config.loginButtonText);

            // Label Mapping
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
            let footer = document.getElementById('custom-footer');
            if (footerText) {
                if (!footer) {
                    footer = document.createElement('div');
                    footer.id = 'custom-footer';
                    footer.style.marginTop = '20px';
                    footer.style.opacity = '0.7';
                    const card = document.querySelector('.card-pf');
                    if (card) card.appendChild(footer);
                }
                footer.innerHTML = footerText;
            } else if (footer) {
                footer.remove();
            }

            // Success: Update valid state
            lastValidConfig = JSON.parse(JSON.stringify(config));
            console.log('[ThemeInjector] Theme applied successfully.');

        } catch (e) {
            console.error('[ThemeInjector] Failed to apply theme. Reverting...', e);
            // Revert Logic
            if (Object.keys(lastValidConfig).length > 0) {
                applyTheme(lastValidConfig);
            }
        }
    };

    // --- 3. Initial Load ---
    const configApi = `/v1/themes/${realm}`;
    fetch(configApi)
        .then(response => {
            if (!response.ok) throw new Error("Config API Failed");
            return response.json();
        })
        .then(config => {
            applyTheme(config);
        })
        .catch(err => {
            console.warn('[ThemeInjector] Could not load theme config, using defaults.', err);
        });

    // --- 4. Live Editor Listener ---
    window.addEventListener('message', (event) => {
        // In production, uncomment and set your allowed origin
        // if (event.origin !== "https://dashboard.keycluster.com") return;

        if (event.data && event.data.type === 'UPDATE_THEME_PREVIEW') {
            console.log('[ThemeInjector] Received Live Preview update');
            applyTheme(event.data.payload);
        }
    });

})();
