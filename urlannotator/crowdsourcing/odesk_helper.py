# Various methods making oDesk API usage more effortless by providing
# shortcut functions for simple tasks.
#
# Ciphertext referenced in various methods' documentation is a unique
# worker identifier similar to it's id. Currently there is no direct way to
# acquire worker's ciphertext other than from direct references in other people
# jobs.
#
# Getting oDesk API Client should be done when multiple API queries are needed
# or non-standard ones.
import odesk
import datetime
import math
import selenium

from django.conf import settings
from django.template.loader import get_template
from django.template import Context
from selenium.webdriver.firefox.webdriver import WebDriver

from urlannotator.main.models import Job, Worker, JOB_STATUS_ACTIVE
from urlannotator.crowdsourcing.models import OdeskJob, OdeskMetaJob
from urlannotator.crowdsourcing.tagasauris_helper import (get_split,
    TAGASAURIS_GATHER_SAMPLES_PER_JOB)
from urlannotator.tools.utils import setting

import logging
log = logging.getLogger(__name__)


def make_odesk_client(token, secret, test=False):
    if test:
        return make_test_client(token, secret)

    return odesk.Client(
        settings.ODESK_SERVER_KEY,
        settings.ODESK_SERVER_SECRET,
        oauth_access_token=token,
        oauth_access_token_secret=secret,
        auth='oauth',
    )


def make_client_from_job(job, test=False):
    return make_client_from_account(account=job.account, test=test)


def make_client_from_account(account, test=False):
    token = account.odesk_token
    secret = account.odesk_secret
    return make_odesk_client(token=token, secret=secret, test=test)


def make_test_client(token=None, secret=None, *args, **kwargs):
    """
        Returns server-authenticated oDesk client.
    """
    return odesk.Client(
        settings.ODESK_SERVER_KEY,
        settings.ODESK_SERVER_SECRET,
        oauth_access_token=settings.ODESK_SERVER_TOKEN_KEY,
        oauth_access_token_secret=settings.ODESK_SERVER_TOKEN_SECRET,
        auth='oauth',
    )


def get_worker_name(ciphertext):
    """
        Returns oDesk worker's name with given `ciphertext`.
    """
    client = make_test_client()
    try:
        r = client.provider.get_provider(ciphertext)
        return r['dev_full_name']
    except:
        return None

# TODO: Proper category and subcategory name here.
NEW_JOB_CATEGORY = 'Sales & Marketing'
NEW_JOB_SUBCATEGORY = 'Other - Sales & Marketing'

# TODO: Proper job visibility
NEW_JOB_TYPE = 'hourly'
NEW_JOB_VISIBILITY = 'invite-only'
NEW_JOB_HOURLY_DURATION = 0

NEW_JOB_DURATION = datetime.timedelta(days=360)

JOB_SAMPLE_GATHERING_KEY = 'sample_gathering'
JOB_VOTING_KEY = 'voting'
JOB_BTM_GATHERING_KEY = 'btm_gathering'
JOB_BTM_VOTING_KEY = 'btm_voting'

SAMPLE_GATHERING_TEAM = '%s Sample Gathering'
VOTING_TEAM = '%s Voting'
BTM_GATHERING_TEAM = '%s Beat The Machine Gathering'
BTM_VOTING_TEAM = '%s Beat The Machine Voting'

TEAM_PREFIX = setting('ODESK_TEAM_PREFIX', 'Build A Classifier')

ODESK_HIT_SPLIT = 20

ODESK_OFFERS_PAGE_SIZE = 200
OFFER_BY_WORKER = 'provider'
OFFER_BY_OWNER = 'buyer'

# Offers made by WORKERS to the OWNER:
# REJECTED - OWNER has rejected WORKER's offer
# IN_PROCESS - Not processed candidacy
# CANCELLED - Worker has cancelled candidacy (withdrew)
OFFER_CANDIDACY_W_REJECTED = 'rejected'
OFFER_CANDIDACY_W_IN_PROCESS = 'in_process'
OFFER_CANDIDACY_W_CANCELLED = 'cancelled'

# Offers made by the OWNER to the WORKER regarding a job:
# FILLED - OWNER has sent it to the WORKER
# IN_PROCESS - WORKER has accepted it
# CANCELLED - WORKER has cancelled (declined) it
# REJECTED - OWNER has withdrawn the offer
OFFER_CANDIDACY_O_FILLED = 'filled'
OFFER_CANDIDACY_O_IN_PROCESS = 'in_process'
OFFER_CANDIDACY_O_CANCELLED = 'cancelled'
OFFER_CANDIDACY_O_REJECTED = 'rejected'


def parse_cipher_from_profile(url):
    """
        Extracts ciphertext from profile url.
    """
    try:
        split = url.rsplit('/', 1)
        if split[1].startswith('~'):
            return split[1]
        return None
    except:
        log.exception(
            '[oDesk] Error while parsing ciphertext from profile %s' % url
        )
        return None


def calculate_job_end_date():
    """
        Calculates job's expiry date.
    """
    date = datetime.datetime.now() + NEW_JOB_DURATION
    # Date in format mm-dd-yyyy, e.g. 06-30-2012
    return date.strftime('%m-%d-%Y')


def notify_workers(odesk_job, hit, job):
    """
        Notifies workers in a job about a new Tagasauris job available.
    """
    worker_ids = map(lambda x: x.user_id, odesk_job.odeskjob_set.all())
    sender = odesk_job.account.odesk_id
    title = get_template('odesk_new_hit_title.txt')
    body = get_template('odesk_new_hit_body.txt')
    hit_url = ''
    if odesk_job.job_type == OdeskMetaJob.ODESK_META_SAMPLE_GATHER:
        hit_url = job.get_sample_gathering_url()
    elif odesk_job.job_type == OdeskMetaJob.ODESK_META_VOTING:
        hit_url = job.get_voting_url()
    elif odesk_job.job_type == OdeskMetaJob.ODESK_META_BTM_GATHER:
        hit_url = job.get_btm_gathering_url()
    elif odesk_job.job_type == OdeskMetaJob.ODESK_META_BTM_VOTING:
        hit_url = job.get_btm_voting_url()

    context = {'hit_url': hit_url}
    title = title.render(Context(context))
    body = body.render(Context(context))

    client = make_client_from_job(job)
    for worker_id in worker_ids:
        client.mc.post_message(username=sender,
            recipients=worker_id, subject=title, body=body)


def send_current_hits(worker_id, odesk_job):
    """
        Sends current hit URLs to a newly invited worker.
    """
    sender = odesk_job.account.odesk_id
    title = get_template('odesk_new_worker_title.txt')
    body = get_template('odesk_new_worker_body.txt')
    hit_urls = []
    for job in odesk_job.account.job_set.filter(status=JOB_STATUS_ACTIVE):
        if odesk_job.job_type == OdeskMetaJob.ODESK_META_SAMPLE_GATHER:
            hit_urls.append(job.get_sample_gathering_url())
        elif odesk_job.job_type == OdeskMetaJob.ODESK_META_VOTING:
            hit_urls.append(job.get_voting_url())
        elif odesk_job.job_type == OdeskMetaJob.ODESK_META_BTM_GATHER:
            hit_urls.append(job.get_btm_gathering_url())
        elif odesk_job.job_type == OdeskMetaJob.ODESK_META_BTM_VOTING:
            hit_urls.append(job.get_btm_voting_url())

    context = {'hit_urls': hit_urls}
    title = title.render(Context(context))
    body = body.render(Context(context))

    client = make_client_from_job(job)
    client.mc.post_message(username=sender,
        recipients=worker_id, subject=title, body=body)


def selenium_login(webdriver):
    """
        Logs a selenium webdriver into odesk.
    """
    webdriver.get('https:/www.odesk.com/login')
    webdriver.find_element_by_id("username").clear()
    webdriver.find_element_by_id("username").send_keys(settings.ODESK_TEST_ACC_NAME)
    webdriver.find_element_by_id("password").clear()
    webdriver.find_element_by_id("password").send_keys(settings.ODESK_TEST_ACC_PASS)
    webdriver.find_element_by_id("submit").click()


def add_odesk_team(webdriver, name):
    try:
        webdriver.find_element_by_css_selector("a.oNavIcon.oNavIconSettings").click()
        webdriver.find_element_by_link_text("Teams").click()
        webdriver.find_element_by_link_text("Create a New Team").click()
        webdriver.find_element_by_id("name").clear()
        webdriver.find_element_by_id("name").send_keys(name)
        webdriver.find_element_by_id("submit").click()
        return True
    except:
        log.exception('[oDesk] Error while creating a team %s' % name)
        return False


def add_odesk_teams(user):
    """
        Creates oDesk teams that will be used for our jobs.
    """
    driver = WebDriver()
    driver.implicitly_wait(time_to_wait=10)

    selenium_login(webdriver=driver)
    account = user.get_profile()

    # Gain access to settings
    driver.find_element_by_css_selector("a.oNavIcon.oNavIconSettings").click()
    driver.find_element_by_id("answer").clear()
    driver.find_element_by_id("answer").send_keys(settings.ODESK_TEST_ACC_ANSWER)
    driver.find_element_by_id("submitButton").click()

    sg_name = SAMPLE_GATHERING_TEAM % TEAM_PREFIX
    v_name = VOTING_TEAM % TEAM_PREFIX
    btmg_name = BTM_GATHERING_TEAM % TEAM_PREFIX
    btmv_name = BTM_VOTING_TEAM % TEAM_PREFIX

    sg_res = add_odesk_team(webdriver=driver, name=sg_name)
    v_res = add_odesk_team(webdriver=driver, name=v_name)
    btmg_res = add_odesk_team(webdriver=driver, name=btmg_name)
    btmv_res = add_odesk_team(webdriver=driver, name=btmv_name)

    if not all([sg_res, v_res, btmg_res, btmv_res]):
        log.warning('[oDesk] Failed to create oDesk teams.')
        return False

    client = make_client_from_account(account)
    teams = client.hr.get_teams()

    for team in teams:
        if team['name'] == sg_name:
            account.odesk_teams[JOB_SAMPLE_GATHERING_KEY] = team['reference']
        elif team['name'] == v_name:
            account.odesk_teams[JOB_VOTING_KEY] = team['reference']
        elif team['name'] == btmg_name:
            account.odesk_teams[JOB_BTM_GATHERING_KEY] = team['reference']
        elif team['name'] == btmv_name:
            account.odesk_teams[JOB_BTM_VOTING_KEY] = team['reference']
    account.save()
    return True


# Below function should work once the oDesk API is fixed, until then we have to
# resort to Selenium because they are using scrambled JS code to generate
# form tokens.
# We have to decline offer, before sending our own
def decline_offer(offer_reference):
    driver = WebDriver()

    # Login
    selenium_login(webdriver=driver)

    # Decline offer
    driver.get('https://www.odesk.com/applications/%s' % offer_reference)
    driver.find_element_by_id("declineButton").click()
    driver.find_element_by_css_selector("option[value=\"146\"]").click()
    driver.find_element_by_id("otherReason").clear()
    driver.find_element_by_id("otherReason").send_keys("Test")
    driver.find_element_by_xpath("(//a[contains(text(),'Decline')])[2]").click()

    driver.close()


def send_offer(cipher, job_reference, client, buyer_reference,
        worker_reference):
    driver = WebDriver()
    driver.implicitly_wait(time_to_wait=10)

    # Login
    selenium_login(webdriver=driver)

    # Worker's page
    driver.get('https:/www.odesk.com/users/%s' % cipher)
    driver.find_element_by_link_text("Contact").click()

    # Make an offer link
    driver.find_element_by_id("jsMakeOfferLink").click()
    el = driver.find_element_by_css_selector("#jsTeamSelector > select")
    el.find_element_by_css_selector("option[value=\"%s\"]" % buyer_reference).\
        click()
    driver.find_element_by_id("jsMakeOfferProceed").click()

    # Sign in to make an offer
    driver.find_element_by_id("password").clear()
    driver.find_element_by_id("password").send_keys(settings.ODESK_TEST_ACC_PASS)
    try:
        driver.find_element_by_id("answer").clear()
        driver.find_element_by_id("answer").send_keys(settings.ODESK_TEST_ACC_ANSWER)
    except selenium.exceptions.NoSuchElementException:
        pass
    driver.find_element_by_id("submitButton").click()

    # Make an offer form
    driver.find_element_by_id("useExistingJob-yes").click()
    el = driver.find_element_by_id("jobPosting")
    el.find_element_by_css_selector("option[value=\"%s\"]" % job_reference).\
        click()
    driver.find_element_by_id("employerRate").clear()
    driver.find_element_by_id("employerRate").send_keys("0.01")
    driver.find_element_by_id("setLimit-yes").click()
    driver.find_element_by_id("limit").clear()
    driver.find_element_by_id("limit").send_keys("0")
    driver.find_element_by_id("submitButton").click()

    # Agreement
    driver.find_element_by_id("agreement").click()
    driver.find_element_by_id("submitButton").click()

    driver.close()

# Below function should work once the oDesk API is fixed, until then we have to
# resort to Selenium because they are using scrambled JS code to generate
# form tokens.
# def send_offer(client, job_reference, buyer_reference=None,
#     worker_reference=None, cipher=None):
#     try:
#         if worker_reference:
#             worker_reference = int(worker_reference)

#         res = client.hr.make_offer(job_reference=int(job_reference),
#             buyer_team_reference=buyer_reference, profile_key=cipher,
#             provider_reference=worker_reference, weekly_hours_limit=0,
#             weekly_stipend_hours=0, weekly_salary_pay_amount=0,
#             weekly_salary_charge_amount=0, hourly_pay_rate=0.01,
#             keep_open=True)
#         return res
#     except Exception, E:
#         log.exception(
#             '[oDesk] Failed to make an offer for worker %s and job %s %s' %
#             (cipher, job_reference, E.hdrs)
#         )
#         return None


def handle_owner_offers(offers, meta_job, odesk_jobs):
    """
        Checks through listed owner `offers` in `meta_job` and updates status
        of them in database.

        :param offers: list of offers from oDesk API
        :param meta_job: `OdeskMetaJob` instance representing oDesk job
        :param odesk_jobs: a dictionary of `OdeskJob` relationships between
                           an oDesk worker and the job
    """
    for offer in offers:
        candidacy_status = offer['candidacy_status']
        cipher = parse_cipher_from_profile(offer['provider__profile_url'])
        oj = odesk_jobs.get(cipher, None)

        if oj is None:
            log.info(
                '[oDesk] Sent offer to worker that didn\'t apply. Skipping'
            )
            continue

        if candidacy_status == OFFER_CANDIDACY_O_FILLED:
            # WORKER has accepted the offer. Process it!
            if not oj.accepted:
                oj.accepted = True
                oj.engagement_id = offer['engagement__reference']
                oj.save()
                send_current_hits(worker_id=oj.user_id,
                    odesk_job=meta_job)
        elif candidacy_status == OFFER_CANDIDACY_O_IN_PROCESS:
            # WORKER hasn't decided yet
            pass
        elif candidacy_status == OFFER_CANDIDACY_O_REJECTED:
            # OWNER has rejected the offer. Process it!
            if not oj.declined:
                oj.declined = True
                oj.save()
        elif candidacy_status == OFFER_CANDIDACY_O_CANCELLED:
            # WORKER has declined the offer. Process it!
            if not oj.declined:
                oj.declined = True
                oj.save()
        else:
            log.info(
                'Unhandled candidacy status %s' % candidacy_status
            )


def handle_worker_offers(offers, meta_job, odesk_jobs, client):
    """
        Checks through listed worker `offers` in `meta_job` and updates status
        of them in database. In addition, sends an offer by the owner to newly
        applied workers, declining theirs' applications. (To make it possible
        to send the second application)

        :param offers: list of offers from oDesk API
        :param meta_job: `OdeskMetaJob` instance representing oDesk job
        :param odesk_jobs: a dictionary of `OdeskJob` relationships between
                           an oDesk worker and the job
    """
    for offer in offers:
        candidacy_status = offer['candidacy_status']
        cipher = parse_cipher_from_profile(offer['provider__profile_url'])
        if candidacy_status == OFFER_CANDIDACY_W_REJECTED:
            # WORKER's candidacy has been rejected by the OWNER
            # Could be that sending an offer failed after declining this one
            # so we have to rehandle.
            worker, new = Worker.objects.get_or_create_odesk(worker_id=cipher)
            oj = odesk_jobs.get(cipher, None)

            # Invitation succeeded
            if oj is not None and oj.invited:
                continue

            send_offer(cipher=cipher, job_reference=meta_job.reference,
                client=client, buyer_reference=meta_job.get_team_reference(),
                worker_reference=offer['provider__reference'])
            if oj is None:
                odesk_jobs[cipher] = OdeskJob.objects.create(meta_job=meta_job,
                    worker=worker, invited=True, user_id=offer['provider__id'])
            else:
                oj.invited = True
                oj.save()
        elif candidacy_status == OFFER_CANDIDACY_W_IN_PROCESS:
            # Not processed WORKER's candidacy
            worker, new = Worker.objects.get_or_create_odesk(worker_id=cipher)
            decline_offer(offer_reference=offer['reference'])
            send_offer(cipher=cipher, job_reference=meta_job.reference,
                client=client, buyer_reference=meta_job.get_team_reference(),
                worker_reference=offer['provider__reference'])
            odesk_jobs[cipher] = OdeskJob.objects.create(meta_job=meta_job,
                worker=worker, invited=True, user_id=offer['provider__id'])
        elif candidacy_status == OFFER_CANDIDACY_W_CANCELLED:
            # WORKER's cancelled offer (by himself)
            pass
        else:
            log.info(
                'Unhandled candidacy status %s' % candidacy_status
            )


def check_odesk_job(odesk_job):
    """
        Checks an oDesk job for new worker applications and handles offers sent
        by us.

        :param odesk_job: `OdeskMetaJob` instance of job to be checked.
    """
    client = make_client_from_account(odesk_job.account)
    try:
        response = client.hr.get_offers(
            buyer_team_reference=odesk_job.get_team_reference(),
            job_ref=odesk_job.reference,
            page_size=ODESK_OFFERS_PAGE_SIZE,
        )
    except:
        log.exception(
            'Error while getting offers for job %s' % odesk_job.reference
        )
        return False

    offset = 0
    total_items = int(response['lister']['total_items'])
    offers_by_owner = []
    offers_by_worker = []
    while (offset < total_items):
        offers = response['offer']
        # Single offer was returned - as a dict, not as a list of 1 element
        # Wrap it so we can handle all cases uniformely
        if type(offers) == dict:
            offers = [offers]

        for offer in offers:
            created_type = offer['created_type']
            if created_type == OFFER_BY_WORKER:
                offers_by_worker.append(offer)
            elif created_type == OFFER_BY_OWNER:
                offers_by_owner.append(offer)

        offset += len(offers)
        try:
            response = client.hr.get_offers(
                buyer_team_reference=odesk_job.get_team_reference(),
                job_ref=odesk_job.reference,
                page_size=ODESK_OFFERS_PAGE_SIZE,
                page_offset=offset,
            )
        except:
            log.exception(
                '[oDesk] Error while getting offers offset %d for job %s' %
                (offset, odesk_job.reference)
            )
            break

    odeskjobs = {}
    for odeskjob in odesk_job.odeskjob_set.filter(worker__isnull=False).\
        select_related('worker').iterator():
        odeskjobs[odeskjob.worker.external_id] = odeskjob

    # First handle offers by owner
    handle_owner_offers(offers=offers_by_owner, meta_job=odesk_job,
        odesk_jobs=odeskjobs)

    # Then offers by workers
    handle_worker_offers(offers=offers_by_worker, meta_job=odesk_job,
        odesk_jobs=odeskjobs, client=client)
    return True


def get_reference(client):
    try:
        r = client.hr.get_teams()
        return r[0]['reference']
    except:
        log.exception('[oDesk] Error while getting client reference')
        return None


def get_voting_split(job):
    """
        Calculates number of workers to accept offers from to voting job.
    """
    # First calculate number of hits
    no_hits = math.ceil(float(job.no_of_urls) / settings.TAGASAURIS_VOTE_MEDIA_PER_HIT)

    # Second, get a square root of the split, floored. Why square root?
    # Because it has diminishing returns - the more work has to be done
    # the lower amount of worker has to be added in comparison to smaller-work
    # jobs.
    # In other works: we wont end up with sky-rocketed amount of workers
    # required to get all urls!
    split = math.sqrt(math.ceil(float(no_hits) / ODESK_HIT_SPLIT))
    return max(split, settings.TAGASAURIS_VOTE_WORKERS_PER_HIT)


def _create_job(title, description, job, team):
    """
        Creates oDesk job with given title and description and returns it's
        reference.
    """
    try:
        token = job.account.odesk_token
        secret = job.account.odesk_secret
        client = make_odesk_client(token, secret)

        data = {
            'buyer_team__reference': team,
            'title': title,
            'job_type': NEW_JOB_TYPE,
            'description': description,
            'duration': NEW_JOB_HOURLY_DURATION,
            'visibility': NEW_JOB_VISIBILITY,
            'category': NEW_JOB_CATEGORY,
            'end_date': calculate_job_end_date(),
            'subcategory': NEW_JOB_SUBCATEGORY,
        }

        response = client.hr.post_job(job_data=data)

        reference = response['job']['reference']
        return reference
    except:
        log.exception(
            '[oDesk] Error while creating job for job %d' % job.id
        )
        return None


def create_sample_gather(job, only_hit=False, *args, **kwargs):
    """
        Creates oDesk sample gathering job according from passed Job object.
    """
    # We are creating a single oDesk job for ALL user jobs.
    if OdeskMetaJob.objects.get_active_sample_gathering().\
        filter(account=job.account).count():
        return False

    try:
        context = {
            'samples_count': TAGASAURIS_GATHER_SAMPLES_PER_JOB,
            'job': job,
        }

        titleTemplate = get_template('odesk_meta_sample_gather_title.txt')
        descriptionTemplate = get_template('odesk_meta_sample_gather_description.txt')

        title = titleTemplate.render(Context(context))
        description = descriptionTemplate.render(Context(context))

        team = job.account.odesk_teams[JOB_SAMPLE_GATHERING_KEY]
        reference_meta = _create_job(title, description, job, team)
        if reference_meta:
            OdeskMetaJob.objects.create_sample_gather(account=job.account,
                reference=reference_meta, workers_to_invite=get_split(job))
        return reference_meta
    except:
        log.exception(
            '[oDesk] Error while creating sample gathering job for job %d' % job.id
        )
        return False


def create_voting(job, only_hit=False, *args, **kwargs):
    # We are creating a single oDesk job for ALL user jobs.
    if OdeskMetaJob.objects.get_active_voting().\
        filter(account=job.account).count():
        return False

    try:
        job = Job.objects.get(id=job.id)

        context = {
            'job': job,
        }

        titleTemplate = get_template('odesk_meta_voting_title.txt')
        descriptionTemplate = get_template('odesk_meta_voting_description.txt')

        title = titleTemplate.render(Context(context))
        description = descriptionTemplate.render(Context(context))

        team = job.account.odesk_teams[JOB_VOTING_KEY]
        reference = _create_job(title, description, job, team)
        if reference:
            OdeskMetaJob.objects.create_voting(account=job.account,
                reference=reference, workers_to_invite=get_voting_split(job))
        return reference
    except:
        log.exception(
            '[oDesk] Error while creating voting job for job %d' % job.id
        )
        return False


def create_btm_gather(topic, description, no_of_urls, job, only_hit=False,
    *args, **kwargs):
    """
        Creates oDesk BTM sample gathering job according from passed Job object.
    """
    # We are creating a single oDesk job for ALL user jobs.
    if OdeskMetaJob.objects.get_active_btm_gather().\
        filter(account=job.account).count():
        return False

    try:
        team = job.account.odesk_teams[JOB_BTM_GATHERING_KEY]
        reference_meta = _create_job(topic, description, job, team)
        if reference_meta:
            OdeskMetaJob.objects.create_btm_gather(account=job.account,
                reference=reference_meta, workers_to_invite=get_split(job))
        return reference_meta
    except:
        log.exception(
            '[oDesk] Error while creating btm gathering job for job %d' % job.id
        )
        return False


def create_btm_voting(job, only_hit=False):
    # We are creating a single oDesk job for ALL user jobs.
    if OdeskMetaJob.objects.get_active_sample_gathering().\
        filter(account=job.account).count():
        return False

    try:
        job = Job.objects.get(id=job.id)

        context = {
            'job': job,
        }

        titleTemplate = get_template('odesk_meta_btm_voting_title.txt')
        descriptionTemplate = get_template('odesk_meta_btm_voting_description.txt')

        title = titleTemplate.render(Context(context))
        description = descriptionTemplate.render(Context(context))

        team = job.account.odesk_teams[JOB_BTM_VOTING_KEY]
        reference = _create_job(title, description, job, team)
        if reference:
            OdeskMetaJob.objects.create_btm_voting(account=job.account,
                reference=reference, workers_to_invite=get_voting_split(job))
        return reference
    except:
        log.exception(
            '[oDesk] Error while creating btm voting job for job %d' % job.id
        )
        return False
