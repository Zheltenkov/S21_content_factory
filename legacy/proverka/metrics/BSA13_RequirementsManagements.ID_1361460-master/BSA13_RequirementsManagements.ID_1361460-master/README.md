## Requirements, quality, task management

Summary:
In this project, you will examine the process of requirements management, product quality management, and task management. You will learn how to identify requirements, establish relationships between them, and track the status of requirements. You will also learn the rules and approaches of quality management, understanding what to test for and how to do it.

💡 [Tap here](https://new.oprosso.net/p/4cb31ec3f47a4596bc758ea1861fb624) **to leave your feedback on the project**. It's anonymous and will help our team make your educational experience better. We recommend completing the survey immediately after the project.

## Contents

1. [Chapter I](#chapter-i) \
   1.1. [Preamble](#11)
2. [Chapter II](#chapter-ii) \
   2.1. [General Rules](#21)
3. [Chapter III](#chapter-iii) \
   3.1. [Requirements Management](#31) \
   3.2. [Testing](#32) \
   3.3. [Task Management](#33)
4. [Chapter IV](#chapter-iv) \
   4.1. [Task 1. Haircut Appointment](#41)
5. [Chapter V](#chapter-v) \
   5.1. [Exercise 00 — Identification of Requirement Types](#51) \
   5.2. [Exercise 01 — Creation of Requirements and Registration](#52) \
   5.3. [Exercise 02 — Creating a Positive Test Case](#53) \
   5.4. [Exercise 03 — Creating a Negative Test Case](#53) \
   5.5. [Exercise 04 — Result Evaluation](#55)

## Instructions 

How to learn at “School 21”:

- Here, you’ll find a unique learning experience with a lot of freedom. You’re given a task and left to find your own way to solve it, using whatever resources work best for you — whether that’s the Internet or AI tools like GigaChat. Just be mindful of information quality: verify, think critically, analyze, and compare.
- Peer-to-peer (P2P) learning is the exchange of knowledge and experience with peers, where everyone acts as both mentor and student. This approach allows you to gain a deeper understanding of the material by learning from one another.
- Feel free to ask for help: around you are peers who are also navigating this path for the first time. Share your own experience and ideas with others.  Join Rocket.Chat to stay updated with the latest community announcements. 
- Your learning is meaningless if you just copy someone else’s solutions. When receiving help from others, always make sure you fully understand the “why”, “how”, and “purpose” behind the solution. Don’t be afraid to make mistakes. 
- Does the task seem impossible? Take a break, get some fresh air and clear your mind — this has helped many people. Maybe after that, the solution will come to you naturally.
- The learning process is just as important as the result. It’s not just about completing the task — it’s about understanding HOW to solve it.

## Chapter I <div id="chapter-i"></div>

![Illustration](misc/images/IMG_1814.jpg)

### Preamble <div id="11"></div>

In the survey and design phases, you have been identifying requirements. For the next stages of development, you should determine the order in which certain requirements will be developed, which functional requirements meet key stakeholder goals, business requirements, and stakeholder requirements. And what state they are in: developed, tested, or not yet in the release, not scheduled for implementation. This is what you will be dealing with in the current project. 

The analyst defines stakeholder requirements, identifies business needs, key stakeholder objectives, and formulates success criteria. After coordinating with the customer, the analyst creates functional requirements for the system, and developers implement the software according to those requirements. Testers then test the software. After defects are corrected, testing is performed, and if the customer is satisfied with the test results, the system is put into production. 

In order for the customer to confirm that the system does what it is supposed to do, it is recommended that the tests be performed by someone who has discussed the design with the customer. Or who can explain the consistency between design and implementation. In both the first and second cases, this is the analyst. In addition, the analyst advises both developers and testers during the work process.

### Literature

1. Karl Wiegers, Joy Beatty, "Software Requirements", 3rd edition, amplified. 
2. Philippe Kruchten "The Rational Unified Process: An Introduction". 
3. "Requirements management tools: software and everything you need to know"  <https://businessyield.com/ru/management/requirements-management-tools/>.
4. Manual for [Jira Software](https://www.atlassian.com/ru/software/jira/guides/getting-started/introduction).
5. Manual for <https://weeek.net/ru/help>.

## Chapter II <div id="chapter-ii"></div>

### General Rules <div id="21"></div>

Before starting, clone the project from GitLab into a repository with the same name.

1. Along the way, you may feel a sense of uncertainty and a severe lack of information: that's OK. Remember, the information in the repository and on Google is always with you. So are your peers and Rocket.Chat. Communicate. Search. Use common sense. Don't be afraid to make mistakes.
2. Pay attention to sources of information. Check. Think. Analyse. Compare. 
3. Look at the text of each assignment. Read it several times. 
4. Read the examples carefully. There may be something in them that is not explicitly stated in the task itself.
5. You may find inconsistencies where something new in the terms of the task or examples conflicts with something you already know. If you come across such an inconsistency, try to work it out. If not, write it down as an open question and find out as you work. Do not leave open questions unanswered. 
6. If a task seems confusing or impossible, it only seems that way. Try to break it down. It is likely that some parts will become clear. 
7. There will be several tasks. Those marked with an asterisk (\*) are for the more meticulous students. These tasks are more difficult and are not compulsory. But doing them will give you extra experience and knowledge.
8. Don't try to fool the system or the people around you. You will fool yourself first.
9. Got a question? Ask your neighbour to the right. If that doesn't help, ask your neighbour on the left.
10. When you use help, you should always understand why and how. Otherwise the help is useless.
11. Always push only to the _develop_ branch! The master branch will be ignored. Work in the _src_ directory.
12. There should be no files in your directory other than those specified in the tasks.

## Chapter III <div id="chapter-iii"></div>

### 1. Requirements Management <div id="31"></div>

In the BSA02 project, you learned about the different types of requirements, their levels, and the relationships between them. 

![](misc/images/img1_eng.png)

In the next projects, we identified and developed different types of requirements:

1) Business requirements; 
2) Stakeholder requirements;
3) Functional requirements for the system;
4) Non-functional requirements.

The requirements of the different levels are interrelated. The following shows how stakeholder requirements for separation of user functions are implemented by functional requirements for access rights to functions. 

Example: User access rights management should be developed. This requirement is described as a stakeholder requirement (which role should perform which functions):

1) which roles interact with the system; 
2) which operations on which entities a role performs on which entities.

But the access control mechanism itself is also described as a functional requirement, and possibly as several different requirements: 

1) where access rights information is located in the system (read access rights of roles); 
2) what the management mechanism is (applying access rights).

For this purpose, stakeholder requirements for the distribution of access rights must be linked to functional requirements describing access rights management. 

The requirements can also be interdependent at the same level. For example, as we saw in the BSA11 project, system functions are related to screen interfaces. Or in BSA12 — quality attributes (non-functional requirements) are related to specific functions.

In addition, even functional requirements should be developed in a certain order so that you don't have to redesign the previously developed requirements during implementation. 

Therefore, you should specify and control the dependencies of requirements and prioritize their development. 

The general scheme of the requirements management process is well illustrated in the book by F. Krachten, Chapter 9 "Requirements Management Process Flowchart". Each requirement has an identifier to provide control over requirements, relationships between requirements (tracing), status of work with requirements, and their implementation. 

Typically, an alphanumeric identifier is used where the letters identify a group of requirements by their types (e.g., business requirements, user requirements, functional requirements, interface requirements, non-functional requirements) and the numeric part of constant length identifies the numbering within the group. 

For example, the Business Requirement Document (BRD) identifier is often used for business requirements. An example of a complete business requirement identifier is BRD000017 (not BRD17). You should also define the characteristics (attributes) and states (statuses) of the requirements. The statuses and attributes of different groups of requirements may be different. The approach to identifying and assigning attributes and states of requirements is well described in chapter 27 of K. Wiegers' book. 

The description of requirements, their identifiers, attributes and states can be stored in a table or database, but it is more useful to store them in a task management tool such as Jira, WEEK or, most effectively, in a requirements management system such as DOORS, ArchiMate and the Russian development, SUTr.  An overview of requirements management systems is given in the article "Requirements management tools: software and everything you need to know" <https://businessyield.com/ru/management/requirements-management-tools/>.

In the current project, you will identify groups of requirements, define their attributes and statuses. Describe the requirements for our tasks and perform tracking for them. 

### 2. Testing <div id="32"></div>

Quality management and testing is a separate major area in software development. The purpose of testing is not to guarantee the quality of the product, but to evaluate it and provide information that can be used to improve the quality. That is, to find and report bugs. 

Since an analyst understands better than other development participants how the system should work, under what conditions what result is expected, they should understand how to prepare data for product quality testing (test cases), how to perform quality testing, and how to describe a defect in the software.

**Software testing** is the examination of the correspondence between the real behavior of a program and its expected behavior. It is performed on a finite set of tests selected in a particular way.

**The purpose of testing** is to confirm or detect software conformance with requirements; to identify defects that should be eliminated before they are discovered by program users.

**Error** is a human action that produces an incorrect result. 

**Failure** is the deviation of a component or system from expected performance, operation, or result.

**Defect** is a flaw in a component or system that may cause the component or system to fail to perform a required function. A defect discovered during execution may cause the component or system to fail.

### 3. Task Management <div id="33"></div>

In addition to requirements management, task management systems (Jira, WEEEK, etc.) are used to manage tasks assigned to executors (analysts, developers, testers). An analyst must be able to read and work on a task assigned to him/her, pass the results to the next executor in the work chain, assign a task to another executor, for example a developer and a tester, register a requirement or an error (defect).

## Chapter IV <div id="chapter-iv"></div>

### Description of tasks

### Task 1. Haircut Appointment <div id="41"></div>

The management of a chain of barbershops decided to implement an online booking system. The main objective is to develop the business by expanding the customer base through the possibility of online registration, as well as to reduce employee labour costs and manual labour by automatically informing customers through communication channels. 

Both registered and unregistered visitors can book an appointment on the website. When making an appointment, they can select the type of service: hairdressing or cosmetology, as well as the service itself, the master and the time from the available intervals. The system should provide automatic sending of reminders to clients through the communication channel chosen by the client (Telegram, WhatsApp, VK, SMS) according to the schedule set by the manager. After receiving a service, the system offers the client to evaluate the service and write suggestions on how to improve the work.

The schedule of masters and the services provided by each master should be entered by the manager, who may be more than one person. This person is also responsible for keeping the schedule up to date and adjusting it if necessary, communicating with customers manually, marking the service, charging and accepting payment, sending the payment data to the accounting department. The manager can also receive reports on completed services and view customer feedback.

Each master has the ability to view the schedule and appointments for their services, as well as customer reviews.

Let's look at the requirements related to the following description in the task:

*The system should provide automatic sending of reminders to clients via communication channels chosen by the client (Telegram, WhatsApp, VK, SMS) according to the schedule set by the manager.* 

**Problem:** Some clients forget that they have an appointment at a barbershop.

**Manager Need:** Remind clients in advance of barbershop appointments.

**Client need**: To be reminded in advance of a barbshop appointment.

**Business Requirement:** Increase the number of clients who sign up for services. Reduce the number of times clients forget the time of their service appointment.

## Chapter V <div id="chapter-v"></div>

### Exercise 00 — Identification of Requirement Types <div id="51"></div>

To apply to Task 1, write out the groups of requirements (by their types) that need to be tracked and define the attributes of the requirements.

1) Identify the types of requirements:
   1) Choose 4 types of requirements:
      1) Business requirements: product goals, objectives, product purpose, business rules; 
      2) User (stakeholder) requirements: UC, US, business process diagrams and descriptions, DB schemas, etc.;
      3) Functional system requirements (system specifications, interfaces);
      4) Non-functional system requirements (quality attributes). 
   2) Assign a three-letter identifier to each requirement type.

2. Define the requirement attributes for each type.
   1) State:
      1) Date, time of creation;
      2) Date, time the requirement was transferred to its current state;
      3) Requirement Status;
      4) Requirement Priority;
      5) Product version in which the requirement is included;
      6) Patch (Sprint, Release) in which the requirement is included. 
   2) Description and Rationale:
      1) Short name of the requirement (no more than 50 characters);
      2) Description of the requirement (150 characters maximum); 
      3) Placement of requirement materials (description, schematic, other) — reference;
      4) Verification method or acceptance criterion;
      5) Source of the requirement, superior requirement (except for business requirements);
      6) Relationship of the requirement. 
   3) Responsible parties:
      1) Current implementer;
      2) Decision maker of the requirement;
      3) Author of the requirement (who registered it).
3. Determine the statuses of the requirement:
   1) New;
   2) Under Analysis;
   3) In development;
   4) In testing;
   5) Included in Patch;
   6) Rejected;
   7) Postponed;
   8) Completed.
4. Deploy a personalized instance of https://weeek.net/ru.
5. Create the above requirements in WEEEK.  
6. Run a scan of WEEEK and place it in the answer.

**Scale from 1 to 5**

### Exercise 01 – Creation of Requirements and Registration <div id="52"></div>

Describe the requirements within the highlighted portion of the sentence in Task 1. Sign up for a personal WEEEK instance and set the parameters. 

1. Describe the stakeholder requirements as UserStores and register them in WEEEK. Decompose the highlighted by cursor into:
   1. the manager informs the customer about the service appointment;
   2. customer's receipt of information about the service appointment;
   3. interactions on notification channels;
   4. customization of the notification schedule. 
2. Describe and register in WEEEK the requirements for system functionality to meet the business requirements for:
   1. customize schedules;
   2. automatically sending reminders.
3. Describe and store in WEEEK the requirements for system interfaces to meet the business requirements for:
   1. customize schedules;
   2. automatically send reminders.
4. Prioritize the requirements.
5. Specify dependencies between different requirement types in WEEEK's Source attribute.
6. In WEEEK, specify the horizontal relationships between requirements in the Relationships attribute.
7. Perform a scan of the WEEEK and place it in the response.

**Scale from 1 to 5**

### Exercise 02 – Creating a Positive Test Case <div id="53"></div>

For Task 1, develop positive test cases for UC02.

For the test case, specify:

1. Alphanumeric identifier;
2. Short name of the test;
3. Priority;
4. Prerequisite (if necessary);
5. Select a Manager — a person from the Employee entity who has a Manager role in the Employee Role entity;
6. Execution steps (instruction, simple and unambiguous, specifying the correct values of the required parameters when executed);
7. Expected Result (ER);
8. Postconditions (how to restore the state before the check);
9. Actual Result (ER).
10. Each case must comply with the principle of repeatability (F.I. Repeatable S.T.).
11. Each case must comply with the principle of Atomicity/Independence (F.Independent.S.T).
12. Presented in tabular form, stored in the cloud.
13. Register a task for testing in WEEEK, provide a link to the test case.

**Scale from 1 to 5**

### Exercise 03 — Creating a Negative Test Case <div id="54"></div>

For Task 1, develop negative test cases for UC02.

For each test case, specify:

1. Alphanumeric identifier;
2. Brief name of the test case;
3. Priority;
4. Precondition, if necessary;
5. Select a manager — a person from the Employee entity who has a manager role in the Employee Role entity;
6. Execution steps (instructions, simple and unambiguous, specifying the correct values of the required parameters when executing);
7. Expected Result (ER);
8. Postconditions (how to restore the state before the check);
9. Actual Result (ER).
10. Each case must comply with the principle of repeatability (F.I. Repeatable S.T.).
11. Each case must comply with the principle of atomicity (F.Independent.S.T).
12. Presented in tabular form, stored in the cloud.
13. Register a task for testing in WEEEK, provide a link to the test case.

**Scale from 1 to 5**

### Exercise 04 – Result Evaluation <div id="55"></div>


For UC02 Task 1, review the results of a successful test case. 

Determine the outcome of the test case and create a defect report if necessary. 

1. After performing steps 1, 2:
   1. The system reports that the selected slot is booked in the system by the client. However, when contacting the client, the client denies having booked that slot.
   2. The system reports that the selected slot is not booked in the system by the client.
2. After performing steps 1-5:
   1. The system assigns the slot to the master, the slot does not appear in the list of available slots;
   2. The system does not assign the slot to the master. This slot can also be selected from the list of free slots during further work.
3. If the result of the test case is negative:
   1. Create a defect report. Describe:
      1. The defect identifier (alphanumeric);
      2. Brief description (one sentence, "what, where, when?");
      3. ** Precondition (if applicable);
      4. Steps to reproduce the bug, not necessarily the same as the text case steps;
      5. Expected Result (ER): the positive result of the test case that is expected after testing;
      6. Actual Result (AR): the resulting performance, highlighting what is different from the ER;
      7. \*Postconditions (how to restore the state before testing);
      8. \*Environment (description of the environment: operating system, browser, screen resolution, etc.);
      9. Priority (urgency of fixing the bug);
      10. \*Importance (criticality for fulfillment);
      11. Description;
      12. Additional materials (screenshots, logs, documents, etc.).
   2. Note the negative result in the AR column and specify the identifier of the registered defect.
4. In case of a positive result of the test case, enter the positive case resolution identifier in the AR column.

**Scale from 1 to 5**
