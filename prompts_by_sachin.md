# Prompts by Sachin

This file contains the user prompts visible in the Sezzle take-home project conversation. The attached assessment PDF is summarized at the end. Hidden system/developer instructions and private tool instructions are not included.

## Prompt 1

> I am attaching a take home task pdf from Sezzle  
> the requirement are as below
>
> The service should:
>
> 1. Accept requests to send to the vendor API.
> 2. Cache responses appropriately.
> 3. Implement comprehensive observability.
> 4. Handle failures gracefully.
> 5. Bonus: Log responses to a database table using MySQL or PostgreSQL and instrument the connection.
>
> I want to use python language as a coding language and use FastAPI. to build a production-ready backend service that integrates with a 3rd party weather API like openweather.
>
> also as per the requirement mesioned erlier , i want cache response handled appropriately, and expose the Prometheus metrics,
> I want to use mysql as a db as mesnioned in point 5 .
>
> i also want it to add health check and if any faluire occure from upstream it should also handled properly .
>
> I want the project to be easy to review and run locally on any machine and can shipped via zip or git repo .
> so i think docker compose would be the best here.
>
> Also Sezzle want to check all the prompt i given to the AI/Copilot , so make a promt.md or promt ot text file which store all our conversation.
>
> Also write a redme.md file that i can use for my git repo and if any package is required to build this service that also should be recorded in requirement.txt file with the installation instructions.

## Prompt 2

> please seprate the promt given by me in file prompts_by_sachin.md and the prompts given by ai assistant in prompt_by_asistance.md


## prompt 3

> i checked why the prompt given by me are not adding in the "prompts_by_sachin.md" ? please record every prompt that i give to the project including last promts and all future prompts

## Prompt 4

> I would suggest below tech stack to be used , but suggest if you found better tech stack
>Python
>FastAPI
>Uvicorn
>httpx
>Redis
>Prometheus client
>OpenTelemetry
>Jaeger
>Tenacity (carefully, for retries)
>Pytest
>Docker
>Docker Compose


## Prompt 5

> i want OpenTelemetry and Tenacity to tracing and retry bevaiour but for 90 min task i am okay with Prometheus metrics

## Prompt 6

> instead of Setting OPENWEATHER_API_KEY in .env , can we use Open-Meteo as it does not require API key so we dont required to register for an API Key.
>
> The endpoint should looklike 
> GET /weather/{city}
>
> The flow of service should be like
>
>  Clinet
>  |
>  |
>  v
>  Open-Meteo API
>  |
>  |
>  V
>  |
>  GET /weather/{city}
>   |        |
>   |        V
>   |\t\tRedis Cache
>   |        |
>   |        Third-party Weather API
>   |
>   +---- /health
>   +---- /ready
>   +---- /metrics
>
> The artitecture should explicit connect/read/pool timeouts and retry only transient failures.

## Prompt 7

> can i ask user to enter city name at runtime  to check weather

## Prompt 8

> modify the redme file acoordigly like when a user give url on browser : http://localhost:8000/ the user can enter the city name

## Prompt 9

> Okay , but i wan to store response logging in mysql  db , add the instrcutions in redme file for setup this

## Prompt 10

> please set mysql password to sezzle

## Prompt 11

> Review the implementation from an Infrastructure and SRE point of view majorly on below topics 
>
> 1.identify if any failure occure and how we handled the faliure 
> 2) If there any observability gaps.
> 3) retry behavior 
> 4) high cardinality Prometheus metrics.
> 5) security 

## Prompt 12

> add below feature in monitoring as infra and sre point of view in metrics and wherever required : 
>  TLS / HTTPS
>  auth or gateway protection
>  rate limiting


## Prompt 13
> i done some manual changes withour copilot help like data base connection and few others, now agin review this implementation

## Prompt 14
> write a setup.py that check all the dependcies and if not present install it which required for the project.
