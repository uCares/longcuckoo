# Copyright (C) 2010-2014 Cuckoo Foundation.
# This file is part of Cuckoo Sandbox - http://www.cuckoosandbox.org
# See the file 'docs/LICENSE' for copying permission.

import sys
import re
import time
import datetime
import os

from django.conf import settings
from django.template import RequestContext
from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.views.decorators.http import require_safe

import pymongo
from bson.objectid import ObjectId
from django.core.exceptions import PermissionDenied
from gridfs import GridFS

sys.path.append(settings.CUCKOO_PATH)

from lib.cuckoo.core.database import Database, TASK_PENDING, TASK_SCHEDULED, TASK_UNSCHEDULED, TASK_RUNNING

results_db = pymongo.connection.Connection(settings.MONGO_HOST, settings.MONGO_PORT).cuckoo
fs = GridFS(results_db)

@require_safe
def index(request):
    db = Database()
    tasks_files = db.list_tasks(limit=50, category="file", not_status=[TASK_PENDING,TASK_SCHEDULED,TASK_UNSCHEDULED])
    tasks_urls = db.list_tasks(limit=50, category="url", not_status=[TASK_PENDING,TASK_SCHEDULED,TASK_UNSCHEDULED])

    analyses_files = []
    analyses_urls = []

    if tasks_files:
        for task in tasks_files:
            new = task.to_dict()
            new["target"] = os.path.basename(new["target"])
            new["sample"] = db.view_sample(new["sample_id"]).to_dict()
            new["pcap_file_id"] = ""
            new["pcap_file_length"] = 0

            report = results_db.analysis.find({"info.id": int(task.id)}, sort=[("_id", pymongo.DESCENDING)])
            if report.count() and "pcap_id" in report[0]["network"]:
                file_object = results_db.fs.files.find_one({"_id": ObjectId(report[0]["network"]["pcap_id"])})
                file_item = fs.get(ObjectId(file_object["_id"]))
                new["pcap_file_id"] = report[0]["network"]["pcap_id"]
                new["pcap_file_length"] = file_item.length

            if db.view_errors(task.id):
                new["errors"] = True

            new["experiment"] = task.experiment

            analyses_files.append(new)

    if tasks_urls:
        for task in tasks_urls:
            new = task.to_dict()

            if db.view_errors(task.id):
                new["errors"] = True

            new["experiment"] = task.experiment

            analyses_urls.append(new)

    return render_to_response("analysis/index.html",
                              {"files": analyses_files, "urls": analyses_urls},
                              context_instance=RequestContext(request))

@require_safe
def experiment(request, experiment_id=None):
    db = Database()

    if experiment_id:
        # Get tasks for the provided experiment
        tasks_files = db.list_tasks(limit=50, category="file", experiment=experiment_id)

        analyses_files = []

        if tasks_files:
            for task in tasks_files:
                new = task.to_dict()
                new["timeout"] = time.strftime('%H:%M:%S', time.gmtime(new["timeout"]))
                new["target"] = os.path.basename(new["target"])
                new["sample"] = db.view_sample(new["sample_id"]).to_dict()
                new["pcap_file_id"] = ""
                new["pcap_file_length"] = 0

                report = results_db.analysis.find({"info.id": int(task.id)}, sort=[("_id", pymongo.DESCENDING)])
                if report.count() and "pcap_id" in report[0]["network"]:
                    file_object = results_db.fs.files.find_one({"_id": ObjectId(report[0]["network"]["pcap_id"])})
                    file_item = fs.get(ObjectId(file_object["_id"]))
                    new["pcap_file_id"] = report[0]["network"]["pcap_id"]
                    new["pcap_file_length"] = file_item.length

                if db.view_errors(task.id):
                    new["errors"] = True

                new["experiment"] = task.experiment

                analyses_files.append(new)

        return render_to_response("analysis/index.html",
                                  {"files": analyses_files},
                                  context_instance=RequestContext(request))
    else:
        # List all experiments
        experiments = db.list_experiments()

        for experiment in experiments:
            experiment.last_task.timeout = datetime.timedelta(seconds=experiment.last_task.timeout).__str__()

        return render_to_response("analysis/experiment.html",
                {"experiments": experiments},
                context_instance=RequestContext(request))

@require_safe
def pending(request):
    db = Database()
    tasks = db.list_tasks(status=[TASK_PENDING,TASK_SCHEDULED,TASK_UNSCHEDULED])

    pending = []
    if tasks:
        for task in tasks:
            pending_task = task.to_dict()
            pending_task["target"] = os.path.basename(pending_task["target"])
            pending_task["added_on"] = datetime.datetime.strptime(pending_task["added_on"], "%Y-%m-%d %H:%M:%S")
            pending_task["experiment"] = task.experiment

            pending.append(pending_task)

    return render_to_response("analysis/pending.html",
                              {"tasks": pending},
                              context_instance=RequestContext(request))

@require_safe
def chunk(request, task_id, pid, pagenum):
    try:
        pid, pagenum = int(pid), int(pagenum)-1
    except:
        raise PermissionDenied

    if request.is_ajax():
        record = results_db.analysis.find_one(
            {
                "info.id": int(task_id),
                "behavior.processes.process_id": pid
            },
            {
                "behavior.processes.process_id": 1,
                "behavior.processes.calls": 1
            }
        )

        if not record:
            raise PermissionDenied

        process = None
        for pdict in record["behavior"]["processes"]:
            if pdict["process_id"] == pid:
                process = pdict

        if not process:
            raise PermissionDenied

        objectid = process["calls"][pagenum]
        chunk = results_db.calls.find_one({"_id": ObjectId(objectid)})

        return render_to_response("analysis/behavior/_chunk.html",
                                  {"chunk": chunk},
                                  context_instance=RequestContext(request))
    else:
        raise PermissionDenied


@require_safe
def filtered_chunk(request, task_id, pid, category):
    """Filters calls for call category.
    @param task_id: cuckoo task id
    @param pid: pid you want calls
    @param category: call category type
    """
    if request.is_ajax():
        # Search calls related to your PID.
        record = results_db.analysis.find_one(
            {"info.id": int(task_id), "behavior.processes.process_id": int(pid)},
            {"behavior.processes.process_id": 1, "behavior.processes.calls": 1}
        )

        if not record:
            raise PermissionDenied

        # Extract embedded document related to your process from response collection.
        process = None
        for pdict in record["behavior"]["processes"]:
            if pdict["process_id"] == int(pid):
                process = pdict

        if not process:
            raise PermissionDenied

        # Create empty process dict for AJAX view.
        filtered_process = {"process_id": pid, "calls": []}

        # Populate dict, fetching data from all calls and selecting only appropriate category.
        for call in process["calls"]:
            chunk = results_db.calls.find_one({"_id": call})
            for call in chunk["calls"]:
                if call["category"] == category:
                    filtered_process["calls"].append(call)

        return render_to_response("analysis/behavior/_chunk.html",
                                  {"chunk": filtered_process},
                                  context_instance=RequestContext(request))
    else:
        raise PermissionDenied

@require_safe
def report(request, task_id):
    report = results_db.analysis.find_one({"info.id": int(task_id)}, sort=[("_id", pymongo.DESCENDING)])

    if not report:
        return render_to_response("error.html",
                                  {"error": "The specified analysis does not exist"},
                                  context_instance=RequestContext(request))

    return render_to_response("analysis/report.html",
                              {"analysis": report},
                              context_instance=RequestContext(request))

@require_safe
def file(request, category, object_id):
    file_object = results_db.fs.files.find_one({"_id": ObjectId(object_id)})

    if file_object:
        content_type = file_object.get("contentType", "application/octet-stream")
        file_item = fs.get(ObjectId(file_object["_id"]))

        file_name = file_item.sha256
        if category == "pcap":
            file_name += ".pcap"
            content_type = "application/vnd.tcpdump.pcap"
        elif category == "screenshot":
            file_name += ".jpg"
        else:
            file_name += ".bin"

        response = HttpResponse(file_item.read(), content_type=content_type)
        response["Content-Disposition"] = "attachment; filename={0}".format(file_name)

        return response
    else:
        return render_to_response("error.html",
                                  {"error": "File not found"},
                                  context_instance=RequestContext(request))

def search(request):
    if "search" in request.POST:
        error = None

        try:
            term, value = request.POST["search"].strip().split(":", 1)
        except ValueError:
            term = ""
            value = request.POST["search"].strip()

        if term:
            # Check on search size.
            if len(value) < 3:
                return render_to_response("analysis/search.html",
                                          {"analyses": None,
                                           "term": request.POST["search"],
                                           "error": "Search term too short, minimum 3 characters required"},
                                          context_instance=RequestContext(request))
            # name:foo or name: foo
            value = value.lstrip()

            # Search logic.
            if term == "name":
                records = results_db.analysis.find({"target.file.name": {"$regex": value, "$options": "-i"}}).sort([["_id", -1]])
            elif term == "type":
                records = results_db.analysis.find({"target.file.type": {"$regex": value, "$options": "-i"}}).sort([["_id", -1]])
            elif term == "string":
                records = results_db.analysis.find({"strings" : {"$regex" : value, "$options" : "-1"}}).sort([["_id", -1]])
            elif term == "ssdeep":
                records = results_db.analysis.find({"target.file.ssdeep": {"$regex": value, "$options": "-i"}}).sort([["_id", -1]])
            elif term == "crc32":
                records = results_db.analysis.find({"target.file.crc32": value}).sort([["_id", -1]])
            elif term == "file":
                records = results_db.analysis.find({"behavior.summary.files": {"$regex": value, "$options": "-i"}}).sort([["_id", -1]])
            elif term == "key":
                records = results_db.analysis.find({"behavior.summary.keys": {"$regex": value, "$options": "-i"}}).sort([["_id", -1]])
            elif term == "mutex":
                records = results_db.analysis.find({"behavior.summary.mutexes": {"$regex": value, "$options": "-i"}}).sort([["_id", -1]])
            elif term == "domain":
                records = results_db.analysis.find({"network.domains.domain": {"$regex": value, "$options": "-i"}}).sort([["_id", -1]])
            elif term == "ip":
                records = results_db.analysis.find({"network.hosts": value}).sort([["_id", -1]])
            elif term == "signature":
                records = results_db.analysis.find({"signatures.description": {"$regex": value, "$options": "-i"}}).sort([["_id", -1]])
            elif term == "url":
                records = results_db.analysis.find({"target.url": value}).sort([["_id", -1]])
            elif term == "imphash":
                records = results_db.analysis.find({"static.pe_imphash": value}).sort([["_id", -1]])
            else:
                return render_to_response("analysis/search.html",
                                          {"analyses": None,
                                           "term": request.POST["search"],
                                           "error": "Invalid search term: %s" % term},
                                          context_instance=RequestContext(request))
        else:
            if re.match(r"^([a-fA-F\d]{32})$", value):
                records = results_db.analysis.find({"target.file.md5": value}).sort([["_id", -1]])
            elif re.match(r"^([a-fA-F\d]{40})$", value):
                records = results_db.analysis.find({"target.file.sha1": value}).sort([["_id", -1]])
            elif re.match(r"^([a-fA-F\d]{64})$", value):
                records = results_db.analysis.find({"target.file.sha256": value}).sort([["_id", -1]])
            elif re.match(r"^([a-fA-F\d]{128})$", value):
                records = results_db.analysis.find({"target.file.sha512": value}).sort([["_id", -1]])
            else:
                return render_to_response("analysis/search.html",
                                          {"analyses": None,
                                           "term": None,
                                           "error": "Unable to recognize the search syntax"},
                                          context_instance=RequestContext(request))

        # Get data from cuckoo db.
        db = Database()
        analyses = []

        for result in records:
            new = db.view_task(result["info"]["id"])

            if not new:
                continue

            new = new.to_dict()

            if result["info"]["category"] == "file":
                if new["sample_id"]:
                    sample = db.view_sample(new["sample_id"])
                    if sample:
                        new["sample"] = sample.to_dict()

            analyses.append(new)

        return render_to_response("analysis/search.html",
                                  {"analyses": analyses,
                                   "term": request.POST["search"],
                                   "error": None},
                                  context_instance=RequestContext(request))
    else:
        return render_to_response("analysis/search.html",
                                  {"analyses": None,
                                   "term": None,
                                   "error": None},
                                  context_instance=RequestContext(request))

@require_safe
def remove(request, task_id):
    """Remove an analysis.
    @todo: remove folder from storage.
    """
    anals = results_db.analysis.find({"info.id": int(task_id)})
    # Only one analysis found, proceed.
    if anals.count() == 1:
        # Delete dups too.
        for analysis in anals:
            # Delete sample if not used.
            if results_db.analysis.find({"target.file_id": ObjectId(analysis["target"]["file_id"])}).count() == 1:
                fs.delete(ObjectId(analysis["target"]["file_id"]))
            # Delete screenshots.
            for shot in analysis["shots"]:
                if results_db.analysis.find({"shots": ObjectId(shot)}).count() == 1:
                    fs.delete(ObjectId(shot))
            # Delete network pcap.
            if "pcap_id" in analysis["network"] and results_db.analysis.find({"network.pcap_id": ObjectId(analysis["network"]["pcap_id"])}).count() == 1:
                fs.delete(ObjectId(analysis["network"]["pcap_id"]))
            # Delete dropped.
            for drop in analysis["dropped"]:
                if "object_id" in drop and results_db.analysis.find({"dropped.object_id": ObjectId(drop["object_id"])}).count() == 1:
                    fs.delete(ObjectId(drop["object_id"]))
            # Delete calls.
            for process in analysis["behavior"]["processes"]:
                for call in process["calls"]:
                    results_db.calls.remove({"_id": ObjectId(call)})
            # Delete analysis data.
            results_db.analysis.remove({"_id": ObjectId(analysis["_id"])})
    elif anals.count() == 0:
        return render_to_response("error.html",
                                  {"error": "The specified analysis does not exist"},
                                  context_instance=RequestContext(request))
    # More analysis found with the same ID, like if process.py was run manually.
    else:
        return render_to_response("error.html",
                                  {"error": "The specified analysis is duplicated in mongo, please check manually"},
                                  context_instance=RequestContext(request))

    # Delete from SQL db.
    db = Database()
    db.delete_task(task_id)

    return render_to_response("success.html",
                              {"message": "Task deleted, thanks for all the fish."},
                              context_instance=RequestContext(request))

def start(request, task_id):
    db = Database()
    db.start_task(task_id)

    return render_to_response("success.html",
            {"message": "Task scheduled for NOW, thanks for all the fish."},
            context_instance=RequestContext(request))

def schedule(request, task_id):
    db = Database()
    task = db.view_task(task_id)
    if task.status == TASK_UNSCHEDULED:
        db.set_status(task_id, TASK_SCHEDULED)

    return render_to_response("success.html",
            {"message": "Task scheduled, thanks for all the fish."},
            context_instance=RequestContext(request))

def unschedule(request, task_id):
    db = Database()
    task = db.view_task(task_id)
    if task.status == TASK_SCHEDULED:
        db.set_status(task_id, TASK_UNSCHEDULED)

    return render_to_response("success.html",
            {"message": "Task unscheduled, thanks for all the fish."},
            context_instance=RequestContext(request))

def terminate(request, task_id):
    db = Database()

    task = db.view_task(task_id)
    db.delete_task(task_id)

    if task.status != TASK_PENDING:
        task_running = db.list_tasks(experiment=task.experiment_id, status=TASK_RUNNING)

        if task_running:
            # Ask the task to free the vm once done
            task_running.repeat = TASK_SINGLE
        else:
            # Free the vm assigned to this experiment
            db.unlock_machine_by_experiment(task.experiment_id)

    if len(db.list_tasks(experiment=task.experiment_id)) == 0:
        # No tasks attached to this experiment, no need to keep the experiment
        db.delete_experiment(task.experiment_id)

    return render_to_response("success.html",
        {"message": "Task terminated, thanks for all the fish."},
        context_instance=RequestContext(request))

