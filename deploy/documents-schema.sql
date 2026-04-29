--
-- PostgreSQL database dump
--

\restrict KDws7f67b1275c8wGfyhCsvqS1NIK7ee8TLU8Tsxr6U3R3aHwpu5F6gPa03hoUW

-- Dumped from database version 16.13 (Debian 16.13-1.pgdg12+1)
-- Dumped by pg_dump version 16.13 (Debian 16.13-1.pgdg13+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    content text NOT NULL,
    embedding public.vector(384),
    source text,
    disease_category text,
    evidence_type text,
    created_at timestamp without time zone DEFAULT now()
);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: documents_disease_category_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX documents_disease_category_idx ON public.documents USING btree (disease_category);


--
-- Name: documents_embedding_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX documents_embedding_idx ON public.documents USING hnsw (embedding public.vector_cosine_ops) WITH (m='16', ef_construction='64');


--
-- PostgreSQL database dump complete
--

\unrestrict KDws7f67b1275c8wGfyhCsvqS1NIK7ee8TLU8Tsxr6U3R3aHwpu5F6gPa03hoUW

