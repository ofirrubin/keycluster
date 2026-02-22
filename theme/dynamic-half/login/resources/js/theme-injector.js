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

    let urlLocale = getParam('ui_locales') || getParam('kc_locale');

    // Default to server-rendered language if available, then system, then 'en'
    if (!urlLocale) {
        if (document.documentElement.lang) {
            urlLocale = document.documentElement.lang;
        } else {
            const navLang = navigator.language || navigator.userLanguage || 'en';
            urlLocale = navLang.split('-')[0];
        }
    }

    const urlThemeOverride = getParam('ui_theme');

    if (urlLocale === 'iw' || urlLocale.startsWith('he')) {
        urlLocale = 'he';
    }

    // --- Message Listener for Live Editor ---
    window.addEventListener('message', function (event) {
        if (!event.data) return;

        switch (event.data.type) {
            case 'UPDATE_THEME_PREVIEW':
                updatePreview(event.data.payload);
                break;
            case 'REQUEST_CURRENT_THEME':
                sendCurrentState();
                break;
        }
    });

    function isSafeUrl(url) {
        if (!url || typeof url !== 'string') return false;
        try {
            var parsed = new URL(url);
            return parsed.protocol === 'https:';
        } catch (e) {
            return false;
        }
    }

    function sanitizeCssValue(val) {
        if (!val || typeof val !== 'string') return '';
        return val.replace(/[;{}()"'\\]/g, '');
    }

    function hexToRgba(hex, alpha) {
        let r = 0, g = 0, b = 0;
        if (!hex) return `rgba(255, 255, 255, ${alpha})`;
        if (hex.startsWith('#')) hex = hex.slice(1);

        if (hex.length === 3) {
            r = parseInt(hex[0] + hex[0], 16);
            g = parseInt(hex[1] + hex[1], 16);
            b = parseInt(hex[2] + hex[2], 16);
        } else if (hex.length === 6) {
            r = parseInt(hex.substring(0, 2), 16);
            g = parseInt(hex.substring(2, 4), 16);
            b = parseInt(hex.substring(4, 6), 16);
        }
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    function updatePreview(payload) {
        const root = document.documentElement;

        if (payload.primaryColor) root.style.setProperty('--primary-color', payload.primaryColor);
        if (payload.secondaryColor) root.style.setProperty('--secondary-color', payload.secondaryColor);

        // Background Type
        if (payload.backgroundType === 'image' && payload.backgroundUrl && isSafeUrl(payload.backgroundUrl)) {
            document.body.style.setProperty('background', `url(${payload.backgroundUrl}) no-repeat center center fixed`, 'important');
            document.body.style.setProperty('background-size', 'cover', 'important');
        } else if (payload.backgroundColor) {
            root.style.setProperty('--background-color', payload.backgroundColor);
            document.body.style.background = payload.backgroundColor;
        }

        // Card Customization
        if (payload.borderRadius !== undefined) root.style.setProperty('--border-radius', payload.borderRadius + 'px');
        if (payload.cardBlur !== undefined) root.style.setProperty('--card-blur', payload.cardBlur + 'px');

        // Dynamic Style Injection for complex overrides (Focus rings, specific classes)
        let dynamicStyle = document.getElementById('theme-live-style');
        if (!dynamicStyle) {
            dynamicStyle = document.createElement('style');
            dynamicStyle.id = 'theme-live-style';
            document.head.appendChild(dynamicStyle);
        }

        let cssRules = '';

        // Input Styling: .pf-c-form-control
        if (payload.inputBorderRadius !== undefined) {
            var ibr = parseInt(payload.inputBorderRadius, 10);
            if (!isNaN(ibr) && ibr >= 0 && ibr <= 50) {
                root.style.setProperty('--input-border-radius', ibr + 'px');
                cssRules += '.pf-c-form-control { border-radius: ' + ibr + 'px !important; } ';
            }
        }

        if (payload.inputFocusColor) {
            var ifc = sanitizeCssValue(payload.inputFocusColor);
            root.style.setProperty('--input-focus-color', ifc);
            cssRules += '.pf-c-form-control:focus { border-bottom-color: ' + ifc + ' !important; box-shadow: 0 0 0 1px ' + ifc + ' !important; } ';
        }

        updateDynamicStyle(dynamicStyle, cssRules);


        // Advanced Styling
        if (payload.iconColor) root.style.setProperty('--icon-color', payload.iconColor);
        if (payload.logoUrl && isSafeUrl(payload.logoUrl)) root.style.setProperty('--logo-url', `url(${payload.logoUrl})`);

        if (payload.cardOpacity !== undefined) {
            const currentBg = payload.cardBg || getComputedStyle(root).getPropertyValue('--card-bg').trim();
            if (payload.cardBg && payload.cardBg.startsWith('#')) {
                root.style.setProperty('--card-bg', hexToRgba(payload.cardBg, payload.cardOpacity));
            } else {
                if (payload.cardBg) root.style.setProperty('--card-bg', hexToRgba(payload.cardBg, payload.cardOpacity));
            }
        } else if (payload.cardBg) {
            root.style.setProperty('--card-bg', payload.cardBg);
        }

        // Texts
        if (payload.loginTitle) {
            const title = document.getElementById('kc-page-title') || document.querySelector('h1.pf-c-title');
            if (title) title.innerText = payload.loginTitle;
        }
        if (payload.loginButtonText) {
            // Target .pf-m-primary.pf-m-block
            const btn = document.querySelector('.pf-c-button.pf-m-primary.pf-m-block') || document.getElementById('kc-login');
            if (btn) {
                if (btn.tagName === 'INPUT') btn.value = payload.loginButtonText;
                else btn.innerText = payload.loginButtonText;
            }
        }
        if (payload.footerText) {
            let footer = document.getElementById('custom-footer');
            if (footer) footer.textContent = payload.footerText;
        }
    }

    // Helper to append rules without wiping existing ones if possible, 
    // but here we regenerate relevant ones. 
    // We only use one style block for live updates to keep it simple.
    function updateDynamicStyle(styleElem, newRules) {
        styleElem.textContent = newRules;
    }

    function sendCurrentState() {
        const root = getComputedStyle(document.documentElement);
        // Best effort to read variables
        const state = {
            primaryColor: root.getPropertyValue('--primary-color').trim(),
            secondaryColor: root.getPropertyValue('--secondary-color').trim(),
            backgroundColor: root.getPropertyValue('--background-color').trim(),
            borderRadius: parseInt(root.getPropertyValue('--border-radius')) || 4,
            inputBorderRadius: parseInt(root.getPropertyValue('--input-border-radius')) || 4,
            cardBlur: parseInt(root.getPropertyValue('--card-blur')) || 0,
            // Note: cardBg might be rgba if opacity was set. 
            // Editor needs to handle this parsing or we send it raw.
            cardBg: root.getPropertyValue('--card-bg').trim(),

            loginTitle: (document.getElementById('kc-page-title') || {}).innerText,
            loginButtonText: (document.getElementById('kc-login') || {}).value || (document.getElementById('kc-login') || {}).innerText,
            footerText: (document.getElementById('custom-footer') || {}).textContent,
        };

        window.parent.postMessage({
            type: 'THEME_STATE',
            payload: state
        }, window.location.origin);
    }

    // --- 2. Initial Application (Immediate) ---
    // Apply immediate overrides from URL before waiting for API

    // RTL Check
    if (urlLocale === 'he' || urlLocale === 'ar') {
        document.documentElement.setAttribute('dir', 'rtl');
        document.documentElement.classList.add('rtl');
        if (document.body) {
            document.body.dir = 'rtl';
        } else {
            document.addEventListener('DOMContentLoaded', () => {
                document.body.dir = 'rtl';
            });
        }
    }

    // Theme Mode Check
    const localMode = urlThemeOverride || 'system';
    const applyMode = (mode) => {
        if (mode === 'dark' || (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
            document.documentElement.classList.add('dark-mode');
            document.documentElement.classList.remove('light-mode');
        } else {
            document.documentElement.classList.add('light-mode');
            document.documentElement.classList.remove('dark-mode');
        }
    };
    applyMode(localMode);

    // ... (Existing message listener code) ...

    const configApi = `${window.location.origin}/v1/themes/${encodeURIComponent(realm)}`;

    fetch(configApi)
        .then(response => response.json())
        .then(config => {
            const root = document.documentElement;
            const translations = config.translations && config.translations[urlLocale] ? config.translations[urlLocale] : {};

            // Styling
            if (config.primaryColor) root.style.setProperty('--primary-color', config.primaryColor);
            if (config.secondaryColor) root.style.setProperty('--secondary-color', config.secondaryColor);
            if (config.backgroundColor) root.style.setProperty('--background-color', config.backgroundColor);
            if (config.borderRadius) root.style.setProperty('--border-radius', config.borderRadius + 'px');
            if (config.fontFamily) root.style.setProperty('--font-family', config.fontFamily);
            if (config.logoUrl && isSafeUrl(config.logoUrl)) root.style.setProperty('--logo-url', `url(${config.logoUrl})`);
            if (config.cardBg) root.style.setProperty('--card-bg', config.cardBg);

            // Re-apply Theme Mode if config specifies it AND no URL override was present
            if (!urlThemeOverride && config.themeMode) {
                applyMode(config.themeMode);
            }

            // Background
            if (config.backgroundUrl && isSafeUrl(config.backgroundUrl)) {
                document.body.style.setProperty('background', `url(${config.backgroundUrl}) no-repeat center center fixed`, 'important');
                document.body.style.setProperty('background-size', 'cover', 'important');
            }

            // Custom CSS
            if (config.customCss) {
                const style = document.createElement('style');
                style.textContent = config.customCss;
                document.head.appendChild(style);
            }

            // Text Overrides & Translations
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

            // Footer
            const footerText = translations.footerText || config.footerText;
            if (footerText) {
                let footer = document.getElementById('custom-footer') || document.createElement('div');
                footer.id = 'custom-footer';
                footer.style.marginTop = '20px';
                footer.style.opacity = '0.7';
                footer.textContent = footerText;

                const card = document.querySelector('.card-pf'); // Keycloak default class
                // If not found, try generic container
                if (card) {
                    if (!document.getElementById('custom-footer')) card.appendChild(footer);
                } else {
                    // Fallback for some themes
                    const loginContainer = document.getElementById('kc-form');
                    if (loginContainer && !document.getElementById('custom-footer')) loginContainer.parentNode.appendChild(footer);
                }
            }
        })
        .catch(err => console.log('Theme config fetch failed, using defaults', err));
})();
