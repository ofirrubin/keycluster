from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Keycluster Theme Mock API")

# Enable CORS so the browser can fetch this from the Keycloak login page
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ThemeConfig(BaseModel):
    # Colors & Layout
    primaryColor: str = "#000000" # Standard black for clean theme
    secondaryColor: str = "#333333"
    backgroundColor: str = "#ffffff" # White background
    backgroundUrl: str | None = None
    backgroundCss: str | None = None
    cardBg: str = "#ffffff"
    borderRadius: int = 4
    fontFamily: str = "Roboto, sans-serif"
    
    # Modes
    # Options: "light", "dark", "system"
    themeMode: str = "light"
    
    # Brand
    logoUrl: str | None = None # No logo by default for clean look
    showRealmName: bool = True
    
    # Content
    footerText: str | None = "Secured by Keycluster"
    customCss: str | None = "" 
    
    # Text Overrides
    loginTitle: str | None = "Sign In"
    loginButtonText: str | None = "Login"
    
    # Dynamic Translations (dictionary for any key)
    translations: dict[str, dict[str, str]] = {
        "en": {
            "loginTitle": "Sign In",
            "loginButtonText": "Login",
            "emailLabel": "Email",
            "passwordLabel": "Password",
            "forgotPassword": "Forgot Password?",
            "footerText": "Secured by Keycluster"
        },
        "he": {
            "loginTitle": "התחברות למערכת",
            "loginButtonText": "כניסה",
            "emailLabel": "אימייל",
            "passwordLabel": "סיסמה",
            "forgotPassword": "שכחתי סיסמה",
            "footerText": "מאובטח על ידי Keycluster"
        },
        "iw": {
            "loginTitle": "התחברות למערכת",
            "loginButtonText": "כניסה",
            "emailLabel": "אימייל",
            "passwordLabel": "סיסמה",
            "forgotPassword": "שכחתי סיסמה",
            "footerText": "מאובטח על ידי Keycluster"
        }
    }

# Global "Live" settings for testing
current_settings = ThemeConfig()

@app.get("/v1/themes/{realm}")
async def get_theme(realm: str):
    return current_settings

@app.post("/v1/themes/{realm}")
async def update_theme(realm: str, config: ThemeConfig):
    global current_settings
    current_settings = config
    return {"status": "updated", "config": current_settings}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
