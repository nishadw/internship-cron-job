import requests
from bs4 import BeautifulSoup
import schedule
import time
import os
import sys
from datetime import datetime
import sendgrid
from sendgrid.helpers.mail import Mail
import certifi
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.safari.options import Options as SafariOptions
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import json

# --- Configuration ---
SCHEDULED_TIME = "18:00"  # 6 PM in 24-hour format

# Updated intern-list.com sources with category descriptions
INTERN_LIST_SOURCES = {
    "All Internships": "https://www.intern-list.com",
    "Engineering Internships": "https://www.intern-list.com/?k=eng",
    "Data/Analytics Internships": "https://www.intern-list.com/?k=da", 
    "AI/ML Internships": "https://www.intern-list.com/?k=aiml",
    "Computer Science/Tech Internships": "https://www.intern-list.com/?k=cst"
}

# IMPORTANT: Replace with your actual SendGrid API key and email
SENDGRID_API_KEY = "YOUR_SENDGRID_API_KEY_HERE"
FROM_EMAIL = "your-email@example.com"
TO_EMAIL = "your-email@example.com"

def setup_selenium_driver():
    """Set up Safari driver with appropriate options for scraping intern-list.com"""
    try:
        driver = webdriver.Safari()
        driver.set_window_size(1920, 1080)
        return driver
    except Exception as e:
        print(f"Error setting up Safari driver: {e}")
        print("Make sure you have:")
        print("1. Safari > Preferences > Advanced > Show Develop menu in menu bar (checked)")
        print("2. Develop > Allow Remote Automation (checked)")
        return None

def scrape_intern_list_with_selenium(url, source_name, is_test=False, max_entries=10):
    """
    Scrape intern-list.com using Selenium to handle the integrated Airtable content
    """
    driver = setup_selenium_driver()
    if not driver:
        return []

    try:
        print(f"Loading {source_name}...")
        driver.get(url)
        
        # Wait for the page to load completely
        wait = WebDriverWait(driver, 20)
        
        # Wait for the Airtable embed or main content to load
        try:
            # Try multiple selectors that might indicate the table has loaded
            selectors_to_wait_for = [
                "iframe[src*='airtable']",  # Airtable iframe
                ".airtable-embed",          # Airtable embed wrapper
                "table",                    # Any table
                "[data-record-id]",         # Airtable record elements
                ".record",                  # Record class
                "tbody tr",                 # Table rows
                "[role='row']"              # ARIA row elements
            ]
            
            element_found = False
            for selector in selectors_to_wait_for:
                try:
                    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    print(f"✅ Found content using selector: {selector}")
                    element_found = True
                    break
                except TimeoutException:
                    continue
            
            if not element_found:
                print("⚠️ Specific content selectors not found, proceeding anyway...")
            
            # Additional wait for dynamic content
            time.sleep(5)
            
        except Exception as e:
            print(f"Warning loading {source_name}: {e}")
            time.sleep(3)  # Still try to scrape

        # Scroll to ensure all content is loaded
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        # Try to find Airtable iframe first
        internships = []
        iframe_found = False
        
        try:
            iframes = driver.find_elements(By.CSS_SELECTOR, "iframe")
            for iframe in iframes:
                src = iframe.get_attribute('src')
                if src and 'airtable' in src.lower():
                    print(f"Found Airtable iframe: {src}")
                    driver.switch_to.frame(iframe)
                    iframe_found = True
                    
                    # Wait for Airtable content inside iframe
                    try:
                        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "tr, [role='row'], .record")))
                        time.sleep(3)
                    except TimeoutException:
                        print("Timeout waiting for Airtable content in iframe")
                    
                    internships = extract_internships_from_airtable(driver, source_name, is_test, max_entries)
                    driver.switch_to.default_content()
                    break
        except Exception as e:
            print(f"Error checking for iframe: {e}")

        # If no iframe found or no data from iframe, try direct page scraping
        if not iframe_found or not internships:
            print("Trying direct page scraping...")
            internships = extract_internships_from_page(driver, source_name, is_test, max_entries)

        print(f"Extracted {len(internships)} internships from {source_name}")
        return internships

    except Exception as e:
        print(f"Error scraping {source_name}: {e}")
        return []
    finally:
        driver.quit()

def extract_internships_from_airtable(driver, source_name, is_test=False, max_entries=10):
    """Extract internships from Airtable content"""
    internships = []
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    # Try multiple selectors for Airtable rows
    row_selectors = [
        "tr[data-row-id]",
        "tbody tr:not(:first-child)",
        "[role='row']:not([role='columnheader'])",
        ".record",
        "tr:has(td)",
        "tr:not(:first-child)"
    ]
    
    rows = []
    for selector in row_selectors:
        try:
            rows = driver.find_elements(By.CSS_SELECTOR, selector)
            if rows:
                print(f"Found {len(rows)} rows using selector: {selector}")
                break
        except:
            continue
    
    if not rows:
        print("No rows found in Airtable")
        return []
    
    rows_to_process = rows[:max_entries] if is_test else rows
    
    for i, row in enumerate(rows_to_process):
        try:
            cells = row.find_elements(By.CSS_SELECTOR, "td, [role='cell'], [role='gridcell']")
            if len(cells) < 3:
                continue
            
            # Extract data (adjust indices based on actual table structure)
            company = cells[0].text.strip() if len(cells) > 0 else ""
            position_title = cells[1].text.strip() if len(cells) > 1 else ""
            date_posted = cells[2].text.strip() if len(cells) > 2 else ""
            
            # Look for apply link
            apply_link = ""
            link_elements = row.find_elements(By.CSS_SELECTOR, "a[href]")
            if link_elements:
                apply_link = link_elements[0].get_attribute('href')
            
            if not company or not position_title:
                continue
            
            # Check date matching
            date_match = is_test or (date_posted and today_str in date_posted)
            
            if date_match:
                internships.append({
                    "company": company,
                    "role": position_title,
                    "link": apply_link or f"https://www.intern-list.com",
                    "source": source_name,
                    "date_posted": date_posted
                })
                
                if is_test and len(internships) >= max_entries:
                    break
            elif not is_test:
                break  # Stop if entries are older
                
        except Exception as e:
            if is_test:
                print(f"Error processing row {i}: {e}")
            continue
    
    return internships

def extract_internships_from_page(driver, source_name, is_test=False, max_entries=10):
    """Extract internships directly from the page (fallback method)"""
    internships = []
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    # Try various selectors for internship listings
    listing_selectors = [
        ".internship-item",
        ".listing",
        ".job-listing",
        "tr:has(td)",
        ".record",
        "[data-record-id]",
        "div[class*='internship']",
        "div[class*='job']"
    ]
    
    listings = []
    for selector in listing_selectors:
        try:
            listings = driver.find_elements(By.CSS_SELECTOR, selector)
            if listings:
                print(f"Found {len(listings)} listings using selector: {selector}")
                break
        except:
            continue
    
    if not listings:
        print("No listings found on page")
        return []
    
    listings_to_process = listings[:max_entries] if is_test else listings
    
    for i, listing in enumerate(listings_to_process):
        try:
            # Try to extract company, role, and date
            company_elements = listing.find_elements(By.CSS_SELECTOR, 
                "td:first-child, .company, [class*='company'], strong, b")
            role_elements = listing.find_elements(By.CSS_SELECTOR,
                "td:nth-child(2), .role, .position, .title, [class*='role'], [class*='position']")
            date_elements = listing.find_elements(By.CSS_SELECTOR,
                "td:last-child, .date, [class*='date'], time")
            link_elements = listing.find_elements(By.CSS_SELECTOR, "a[href]")
            
            company = company_elements[0].text.strip() if company_elements else ""
            role = role_elements[0].text.strip() if role_elements else ""
            date_posted = date_elements[0].text.strip() if date_elements else ""
            apply_link = link_elements[0].get_attribute('href') if link_elements else ""
            
            if not company or not role:
                # Try alternative extraction
                all_text = listing.text.strip().split('\n')
                if len(all_text) >= 2:
                    company = all_text[0]
                    role = all_text[1]
                    date_posted = all_text[-1] if len(all_text) > 2 else ""
            
            if company and role:
                date_match = is_test or (date_posted and today_str in date_posted)
                
                if date_match:
                    internships.append({
                        "company": company,
                        "role": role,
                        "link": apply_link or f"https://www.intern-list.com",
                        "source": source_name,
                        "date_posted": date_posted
                    })
                    
                    if is_test and len(internships) >= max_entries:
                        break
                elif not is_test:
                    break
                    
        except Exception as e:
            if is_test:
                print(f"Error processing listing {i}: {e}")
            continue
    
    return internships

def scrape_todays_internships_from_url(url, source_name, is_test=False):
    """
    Main scraping function for intern-list.com
    """
    return scrape_intern_list_with_selenium(url, source_name, is_test)

def send_email(todays_internships):
    """Send email with internship digest"""
    if not SENDGRID_API_KEY or SENDGRID_API_KEY == "YOUR_SENDGRID_API_KEY_HERE":
        print("⚠️  SendGrid API key not configured. Email not sent.")
        print("Please update SENDGRID_API_KEY with your actual API key.")
        return
    
    date_str = datetime.now().strftime('%b %d, %Y')
    
    if todays_internships:
        subject = f"✅ Daily Internship Digest - {len(todays_internships)} New Roles Found!"
        html_content = f"""
        <html><head><style>
            body {{ font-family: Arial, sans-serif; margin: 20px; }}
            h2 {{ color: #333; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
            th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
            th {{ background-color: #f2f2f2; font-weight: bold; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            a {{ color: #0066cc; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
        </style></head><body>
            <h2>🎯 Internships Posted on {date_str}</h2>
            <p>Found {len(todays_internships)} new internship opportunities from intern-list.com!</p>
            <table>
                <tr><th>Company</th><th>Role</th><th>Source</th><th>Date Posted</th><th>Apply</th></tr>
        """
        for intern in todays_internships:
            html_content += f"""
                <tr>
                    <td>{intern['company']}</td>
                    <td>{intern['role']}</td>
                    <td>{intern['source']}</td>
                    <td>{intern.get('date_posted', 'N/A')}</td>
                    <td><a href='{intern['link']}' target='_blank'>Apply Now</a></td>
                </tr>
            """
        html_content += """
            </table>
            <p style='margin-top: 20px; color: #666; font-size: 14px;'>
                Good luck with your applications! 🚀<br>
                <em>Data sourced from intern-list.com</em>
            </p>
        </body></html>
        """
    else:
        subject = f"👍 Internship Digest: No New Postings Found for {date_str}"
        html_content = f"""
        <html><body style='font-family: Arial, sans-serif; margin: 20px;'>
            <h2>📊 Daily Internship Check Complete</h2>
            <p>Your script ran successfully and checked intern-list.com sources for internships posted today ({date_str}).</p>
            <p><strong>Result:</strong> No new internships found with today's date.</p>
            <p style='color: #666; font-size: 14px;'>Keep checking back - new opportunities are posted regularly!</p>
        </body></html>
    """
    
    message = Mail(
        from_email=FROM_EMAIL,
        to_emails=[TO_EMAIL],
        subject=subject,
        html_content=html_content
    )
    
    try:
        os.environ['SSL_CERT_FILE'] = certifi.where()
        sg = sendgrid.SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"📧 Email sent successfully! (Status: {response.status_code})")
    except Exception as e:
        print(f"❌ Error sending email: {e}")

def run_job(is_test=False):
    """Main job function"""
    job_type = "Test Run" if is_test else "Daily Job"
    print(f"\n🚀 Starting {job_type} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    all_todays_internships = []
    
    for source_title, url in INTERN_LIST_SOURCES.items():
        print(f"\n📊 Scraping '{source_title}'...")
        internships_from_source = scrape_todays_internships_from_url(url, source_title, is_test)
        
        if internships_from_source:
            print(f"✅ Found {len(internships_from_source)} internships")
            # Remove duplicates based on company + role combination
            for internship in internships_from_source:
                duplicate = False
                for existing in all_todays_internships:
                    if (existing['company'].lower() == internship['company'].lower() and 
                        existing['role'].lower() == internship['role'].lower()):
                        duplicate = True
                        break
                if not duplicate:
                    all_todays_internships.append(internship)
        else:
            print("❌ No internships found")
    
    print(f"\n📈 Total unique internships found: {len(all_todays_internships)}")
    print("📧 Sending email digest...")
    send_email(all_todays_internships)
    print(f"✅ {job_type} completed!\n")

# --- Main Execution ---
if __name__ == "__main__":
    # Check if required packages are installed
    try:
        from selenium import webdriver
        print("✅ Selenium is available")
        
        # Test Safari driver
        try:
            test_driver = webdriver.Safari()
            test_driver.quit()
            print("✅ Safari WebDriver is properly configured")
        except Exception as e:
            print(f"❌ Safari WebDriver configuration issue: {e}")
            print("Please ensure:")
            print("1. Safari > Preferences > Advanced > Show Develop menu in menu bar")
            print("2. Develop > Allow Remote Automation")
            
    except ImportError:
        print("❌ Selenium not installed. Install with: pip install selenium")
        sys.exit(1)
    
    if len(sys.argv) > 1 and sys.argv[1].lower() == 'test':
        print("🧪 Running in TEST mode - will get recent internships regardless of date")
        run_job(is_test=True)
    else:
        print(f"⏰ Starting scheduled internship notifier for intern-list.com")
        print(f"📅 Daily emails will be sent at {SCHEDULED_TIME} (6 PM)")
        print("🛑 Press Ctrl+C to stop")
        print(f"🔧 Make sure to update your SendGrid API key and email addresses!")
        
        schedule.every().day.at(SCHEDULED_TIME).do(run_job)
        
        # Run once immediately to test
        print("\n🏃 Running initial test...")
        run_job(is_test=True)
        
        while True:
            try:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
            except KeyboardInterrupt:
                print("\n👋 Script stopped by user")
                break